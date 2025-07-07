# MVCC in PostgreSQL — 3. Row Versions

> Source: [MVCC in PostgreSQL — 3. Row Versions](https://postgrespro.com/blog/pgsql/5967892)

Well, we've already discussed [isolation](./MVCC_in_PostgreSQL_1_Isolation.md) and made a digression regarding [the low-level data structure](https://postgrespro.com/blog/pgsql/5967858). And we've finally reached the most fascinating thing, that is, row versions (tuples).

## Tuple header

As already mentioned, several versions of each row can be simultaneously available in the database. And we need to somehow distinguish one version from another one. To this end, each version is labeled with its effective "time" (`xmin`) and expiration "time" (`xmax`). Quotation marks denote that a special incrementing counter is used rather than the time itself. And this counter is _the transaction identifier_.

(As usual, in reality this is more complicated: the transaction ID cannot always increment due to a limited bit depth of the counter. But we will explore more details of this when our discussion reaches freezing.)

When a row is created, the value of `xmin` is set equal to the ID of the transaction that performed the INSERT command, while `xmax` is not filled in.

When a row is deleted, the `xmax` value of the current version is labeled with the ID of the transaction that performed DELETE.

An UPDATE command actually performs two subsequent operations: DELETE and INSERT. In the current version of the row, `xmax` is set equal to the ID of the transaction that performed UPDATE. Then a new version of the same row is created, in which the value of `xmin` is the same as `xmax` of the previous version.

`xmin` and `xmax` fields are included in the header of a row version. In addition to these fields, the tuple header contains other ones, such as:

*   `infomask` -- several bits that determine the properties of a given tuple. There are quite a few of them, and we will discuss each over time.
*   `ctid` -- a reference to the next, more recent, version of the same row. `ctid` of the newest, up-to-date, row version references that very version. The number is in the `(x,y)` form, where `x` is the number of the page and `y` is the order number of the pointer in the array.
*   The NULLs bitmap, which marks those columns of a given version that contain a NULL. NULL is not a regular value of data types, and therefore, we have to store this characteristic separately.

As a result, the header appears pretty large: 23 bytes per each tuple at a minimum, but usually larger because of the NULLs bitmap. If a table is "narrow" (that is, it contains few columns), the overhead bytes can occupy more space that the useful information.

## Insert

Let's look in more detail at how the operations on rows are performed at a low level, and we start with an insert.

To experiment, we will create a new table with two columns and an index on one of them:

    => CREATE TABLE t(
      id serial,
      s text
    );
    => CREATE INDEX ON t(s);

We start a transaction to insert a row.

    => BEGIN;
    => INSERT INTO t(s) VALUES ('FOO');

This is the ID of our current transaction:

    => SELECT txid_current();

     txid_current 
    --------------
             3664
    (1 row)

Let's look into the contents of the page. The `heap_page_items` function from the "pageinspect" extension enables us to get information on the pointers and row versions:

    => SELECT * FROM heap_page_items(get_raw_page('t',0)) \gx

    -[ RECORD 1 ]-------------------
    lp          | 1
    lp_off      | 8160
    lp_flags    | 1
    lp_len      | 32
    t_xmin      | 3664
    t_xmax      | 0
    t_field3    | 0
    t_ctid      | (0,1)
    t_infomask2 | 2
    t_infomask  | 2050
    t_hoff      | 24
    t_bits      | 
    t_oid       | 
    t_data      | \x0100000009464f4f

Note that the word "heap" in PostgreSQL denotes tables. This is one more weird usage of a term: a heap is a known [data structure](https://en.wikipedia.org/wiki/Heap_(data_structure)), which has nothing to do with a table. This word is used here in the sense that "all is heaped up", unlike in ordered indexes.

This function shows the data "as is", in a format that is difficult to comprehend. To clarify the things, we leave only part of the information and interpret it:

    => SELECT '(0,'||lp||')' AS ctid,
           CASE lp_flags
             WHEN 0 THEN 'unused'
             WHEN 1 THEN 'normal'
             WHEN 2 THEN 'redirect to '||lp_off
             WHEN 3 THEN 'dead'
           END AS state,
           t_xmin as xmin,
           t_xmax as xmax,
           (t_infomask & 256) > 0  AS xmin_commited,
           (t_infomask & 512) > 0  AS xmin_aborted,
           (t_infomask & 1024) > 0 AS xmax_commited,
           (t_infomask & 2048) > 0 AS xmax_aborted,
           t_ctid
    FROM heap_page_items(get_raw_page('t',0)) \gx

    -[ RECORD 1 ]-+-------
    ctid          | (0,1)
    state         | normal
    xmin          | 3664
    xmax          | 0
    xmin_commited | f
    xmin_aborted  | f
    xmax_commited | f
    xmax_aborted  | t
    t_ctid        | (0,1)

We did the following:

*   Added a zero to the pointer number to make it look like a `t_ctid`: (page number, pointer number).
*   Interpreted the status of the `lp_flags` pointer. It is "normal" here, which means that the pointer actually references a row version. We will discuss other values later.
*   Of all information bits, we selected only two pairs so far. `xmin_committed` and `xmin_aborted` bits show whether the transaction with the ID `xmin` is committed (rolled back). A pair of similar bits relates to the transaction with the ID `xmax`.

What do we observe? When a row is inserted, in the table page a pointer appears that has number 1 and references the first and the only version of the row.

The `xmin` field in the tuple is filled with the ID of the current transaction. Because the transaction is still active, both `xmin_committed` and `xmin_aborted` bits are unset.

The `ctid` field of the row version references the same row. It means that no newer version is available.

The `xmax` field is filled with the conventional number 0 since the tuple is not deleted, that is, up-to-date. Transactions will ignore this number because of the `xmax_aborted` bit set.

Let's move one more step to improving the readability by appending information bits to transaction IDs. And let's create the function since we will need the query more than once:

    => CREATE FUNCTION heap_page(relname text, pageno integer)
    RETURNS TABLE(ctid tid, state text, xmin text, xmax text, t_ctid tid)
    AS $
    SELECT (pageno,lp)::text::tid AS ctid,
           CASE lp_flags
             WHEN 0 THEN 'unused'
             WHEN 1 THEN 'normal'
             WHEN 2 THEN 'redirect to '||lp_off
             WHEN 3 THEN 'dead'
           END AS state,
           t_xmin || CASE
             WHEN (t_infomask & 256) > 0 THEN ' (c)'
             WHEN (t_infomask & 512) > 0 THEN ' (a)'
             ELSE ''
           END AS xmin,
           t_xmax || CASE
             WHEN (t_infomask & 1024) > 0 THEN ' (c)'
             WHEN (t_infomask & 2048) > 0 THEN ' (a)'
             ELSE ''
           END AS xmax,
           t_ctid
    FROM heap_page_items(get_raw_page(relname,pageno))
    ORDER BY lp;
    $ LANGUAGE SQL;

What is happening in the header of the row version it is much clearer in this form:

    => SELECT * FROM heap_page('t',0);

     ctid  | state  | xmin | xmax  | t_ctid 
    -------+--------+------+-------+--------
     (0,1) | normal | 3664 | 0 (a) | (0,1)
    (1 row)

We can get similar information, but far less detailed, from the table itself by using `xmin` and `xmax` pseudo-columns:

    => SELECT xmin, xmax, * FROM t;

     xmin | xmax | id |  s  
    ------+------+----+-----
     3664 |    0 |  1 | FOO
    (1 row)

## Commit

When a transaction is successful, its status must be remembered, that is, the transaction must be marked as committed. To this end, the XACT structure is used. (Before version 10 it was called CLOG (commit log), and you are still likely to come across this name.)

XACT is not a table of the system catalog, but files in the PGDATA/pg_xact directory. Two bits are allocated in these files for each transaction — "committed" and "aborted" — exactly the same way as in the tuple header. This information is spread across several files only for convenience; we will get back to this when we discuss freezing. PostgreSQL works with these files page by page, as with all others.

So, when a transaction is committed, the "committed" bit is set for this transaction in XACT. And this is all that happens when the transaction is committed (although we do not mention the write-ahead log yet).

When some other transaction accesses the table page we were just looking at, the former will have to answer a few questions.

1.  Was the transaction `xmin` completed? If not, the created tuple must not be visible.  
    This is checked by looking through another structure, which is located in the shared memory of the instance and called ProcArray. This structure holds a list of all active processes, along with the ID of the current (active) transaction for each.
2.  If the transaction was completed, then was it committed or rolled back? If it was rolled back, the tuple must not be visible either.  
    This is just what XACT is needed for. But it is expensive to check XACT each time, although last pages of XACT are stored in buffers in the shared memory. Therefore, once figured out, the transaction status is written to `xmin_committed` and `xmin_aborted` bits of the tuple. If any of these bits is set, the transaction status is treated as known and the next transaction will not need to check XACT.

Why does not the transaction that performs the insert set these bits? When an insert is being performed, the transaction is yet unaware of whether it will be completed successfully. And at the commit time it's already unclear which rows and in which pages were changed. There can be a lot of such pages, and it is impractical to keep track of them. Besides, some of the pages can be evicted to disk from the buffer cache; to read them again in order to change the bits would mean a considerable slowdown of the commit.

The reverse side of the cost saving is that after the updates, any transaction (even the one performing SELECT) can begin changing data pages in the buffer cache.

So, we commit the change.

    => COMMIT;

Nothing has changed in the page (but we know that the transactions status is already written to XACT):

    => SELECT * FROM heap_page('t',0);

     ctid  | state  | xmin | xmax  | t_ctid 
    -------+--------+------+-------+--------
     (0,1) | normal | 3664 | 0 (a) | (0,1)
    (1 row)

Now a transaction that first accesses the page will need to determine the status of the transaction `xmin` and will write it to the information bits:

    => SELECT * FROM t;

     id |  s  
    ----+-----
      1 | FOO
    (1 row)

    => SELECT * FROM heap_page('t',0);

     ctid  | state  |   xmin   | xmax  | t_ctid 
    -------+--------+----------+-------+--------
     (0,1) | normal | 3664 (c) | 0 (a) | (0,1)
    (1 row)

## Delete

When a row is deleted, the ID of the current deleting transaction is written to the `xmax` field of the up-to-date version and the `xmax_aborted` bit is reset.

Note that the value of `xmax` corresponding to the active transaction works as a row lock. If another transaction is going to update or delete this row, it will have to wait until the `xmax` transaction completes. We will talk about locks in more detail later. At this point, only note that the number of row locks is not limited at all. They do not occupy memory, and the system performance is not affected by that number. However, long lasting transactions have other drawbacks, which will also be discussed later.

Let's delete a row.

    => BEGIN;
    => DELETE FROM t;
    => SELECT txid_current();

     txid_current 
    --------------
             3665
    (1 row)

We see that the transaction ID is written to the `xmax` field, but information bits are unset:

    => SELECT * FROM heap_page('t',0);

     ctid  | state  |   xmin   | xmax | t_ctid 
    -------+--------+----------+------+--------
     (0,1) | normal | 3664 (c) | 3665 | (0,1)
    (1 row)

## Abort

Abort of a transaction works similarly to commit, except that the "aborted" bit is set in XACT. An abort is done as fast as a commit. Although the command is called ROLLBACK, the changes are not rolled back: everything that the transaction has already changed, remains untouched.

    => ROLLBACK;
    => SELECT * FROM heap_page('t',0);

     ctid  | state  |   xmin   | xmax | t_ctid 
    -------+--------+----------+------+--------
     (0,1) | normal | 3664 (c) | 3665 | (0,1)
    (1 row)

When accessing the page, the status will be checked and the hint bit `xmax_aborted` will be set. Although the number `xmax` itself will be still in the page, it will not be looked at.

    => SELECT * FROM t;

     id |  s  
    ----+-----
      1 | FOO
    (1 row)

    => SELECT * FROM heap_page('t',0);

     ctid  | state  |   xmin   |   xmax   | t_ctid 
    -------+--------+----------+----------+--------
     (0,1) | normal | 3664 (c) | 3665 (a) | (0,1)
    (1 row)

## Update

An update works as if the current version is deleted first and then a new one is inserted.

    => BEGIN;
    => UPDATE t SET s = 'BAR';
    => SELECT txid_current();

     txid_current 
    --------------
             3666
    (1 row)

The query returns one row (the new version):

    => SELECT * FROM t;

     id |  s  
    ----+-----
      1 | BAR
    (1 row)

But we can see both versions in the page:

    => SELECT * FROM heap_page('t',0);

     ctid  | state  |   xmin   | xmax  | t_ctid 
    -------+--------+----------+-------+--------
     (0,1) | normal | 3664 (c) | 3666  | (0,2)
     (0,2) | normal | 3666     | 0 (a) | (0,2)
    (2 rows)

The deleted version is labeled with the ID of the current transaction in the `xmax` field. Moreover, this value has overwritten the old one since the previous transaction was rolled back. And the `xmax_aborted` bit is reset since the status of the current transaction is unknown yet.

The first version of the row is now referencing the second, as a newer one.

The index page now contains the second pointer and second row, which references the second version in the table page.

The same way as for a delete, the value of `xmax` in the first version indicates that the row is locked.

Lastly, we commit the transaction.

    => COMMIT;

## Indexes

We were talking only about table pages so far. But what happens inside indexes?

Information in index pages highly depends on the specific index type. Moreover, even one type of indexes can have different kinds of pages. For example: a B-tree has the metadata page and "normal" pages.

Nevertheless, an index page usually has an array of pointers to the rows and rows themselves (just like table pages). Besides, some space at the end of a page is allocated for special data.

Rows in indexes can also have different structures depending on the index type. For example: in an B-tree, rows pertinent to leaf pages contain the value of the indexing key and a reference (`ctid`) to the appropriate table row. In general, an index can be structured quite a different way.

The main point is that in indexes of any type there are no row _versions_. Or we can consider each row to be represented by only one version. In other words, the header of the index row does not contain the `xmin` and `xmax` fields. For now we can assume that references from the index point to all versions of table rows. So to make out which of the row versions are visible to a transaction, PostgreSQL needs to look into the table. (As usual, this is not the whole story. Sometimes the visibility map enables optimizing the process, but we will discuss this later.)

Here, in the index page, we find pointers to both versions: the up-to-date and previous:

    => SELECT itemoffset, ctid FROM bt_page_items('t_s_idx',1);

     itemoffset | ctid  
    ------------+-------
              1 | (0,2)
              2 | (0,1)
    (2 rows)

## Virtual transactions

In practice, PostgreSQL takes advantage of an optimization that permits to "sparingly" expends transaction IDs.

If a transaction only reads data, it does not affect the visibility of tuple at all. Therefore, first the backend process assigns a virtual ID (virtual xid) to the transaction. This ID consists of the process identifier and a sequential number.

Assignment of this virtual ID does not require synchronization between all the processes and is therefore performed very quickly. We will learn another reason of using virtual IDs when we discuss freezing.

Data snapshots do not take into account virtual ID at all.

At different points in time, the system can have virtual transactions with IDs that were already used, and this is fine. But this ID cannot be written to data pages since when the page is accessed next time, the ID can become meaningless.

    => BEGIN;
    => SELECT txid_current_if_assigned();

     txid_current_if_assigned 
    --------------------------

    (1 row)

But if a transaction begins to change data, it receives a true, unique, transaction ID.

    => UPDATE accounts SET amount = amount - 1.00;
    => SELECT txid_current_if_assigned();

     txid_current_if_assigned 
    --------------------------
                         3667
    (1 row)

    => COMMIT;

## Subtransactions

### Savepoints

In SQL, _savepoints_ are defined, which permit rolling back some operations of the transaction without its complete abortion. But this is incompatible with the above model since the transaction status is one for all the changes and no data is physically rolled back.

To implement this functionality, a transaction with a savepoint is divided into several separate _subtransactions_ whose statuses can be managed separately.

Subtrabsactions have their own IDs (greater than the ID of the main transaction). The statuses of subtransactions are written to XACT in a usual way, but the final status depends on the status of the main transaction: if it is rolled back, all subtransactions are rolled back as well.

Information about subtransactions nesting is stored in files of the PGDATA/pg_subtrans directory. These files are accessed through buffers in the shared memory of the instance, which are structured the same way as XACT buffers.

Do not confuse subtransactions with autonomous transactions. Autonomous transactions in no way depend on one another, while subtransactions do depend. There are no autonomous transactions in the regular PostgreSQL, which is, perhaps, for the better: they are actually needed extremely rarely, and their availability in other DBMS invites abuse, which everyone suffers.

Let's clear the table, start a transaction and insert a row:

    => TRUNCATE TABLE t;
    => BEGIN;
    => INSERT INTO t(s) VALUES ('FOO');
    => SELECT txid_current();

     txid_current 
    --------------
             3669
    (1 row)

    => SELECT xmin, xmax, * FROM t;

     xmin | xmax | id |  s  
    ------+------+----+-----
     3669 |    0 |  2 | FOO
    (1 row)

    => SELECT * FROM heap_page('t',0);

     ctid  | state  | xmin | xmax  | t_ctid 
    -------+--------+------+-------+--------
     (0,1) | normal | 3669 | 0 (a) | (0,1)
    (1 row)

Now we establish a savepoint and insert another row.

    => SAVEPOINT sp;
    => INSERT INTO t(s) VALUES ('XYZ');
    => SELECT txid_current();

     txid_current 
    --------------
             3669
    (1 row)

Note that the `txid_current` function returns the ID of the main transaction rather than of the subtransaction.

    => SELECT xmin, xmax, * FROM t;

     xmin | xmax | id |  s  
    ------+------+----+-----
     3669 |    0 |  2 | FOO
     3670 |    0 |  3 | XYZ
    (2 rows)

    => SELECT * FROM heap_page('t',0);

     ctid  | state  | xmin | xmax  | t_ctid 
    -------+--------+------+-------+--------
     (0,1) | normal | 3669 | 0 (a) | (0,1)
     (0,2) | normal | 3670 | 0 (a) | (0,2)
    (2 rows)

Let's rollback to the savepoint and insert the third row.

    => ROLLBACK TO sp;
    => INSERT INTO t VALUES ('BAR');
    => SELECT xmin, xmax, * FROM t;

     xmin | xmax | id |  s  
    ------+------+----+-----
     3669 |    0 |  2 | FOO
     3671 |    0 |  4 | BAR
    (2 rows)

    => SELECT * FROM heap_page('t',0);

     ctid  | state  |   xmin   | xmax  | t_ctid 
    -------+--------+----------+-------+--------
     (0,1) | normal | 3669     | 0 (a) | (0,1)
     (0,2) | normal | 3670 (a) | 0 (a) | (0,2)
     (0,3) | normal | 3671     | 0 (a) | (0,3)
    (3 rows)

In the page, we continue to see the row that was added by the rolled back subtransaction.

Committing the changes.

    => COMMIT;
    => SELECT xmin, xmax, * FROM t;

     xmin | xmax | id |  s  
    ------+------+----+-----
     3669 |    0 |  2 | FOO
     3671 |    0 |  4 | BAR
    (2 rows)

    => SELECT * FROM heap_page('t',0);

     ctid  | state  |   xmin   | xmax  | t_ctid 
    -------+--------+----------+-------+--------
     (0,1) | normal | 3669 (c) | 0 (a) | (0,1)
     (0,2) | normal | 3670 (a) | 0 (a) | (0,2)
     (0,3) | normal | 3671 (c) | 0 (a) | (0,3)
    (3 rows)

It is clearly seen now that each subtransaction has its own status.

Note that SQL does not permit explicit use of subtransactions, that is, you cannot start a new transaction before you complete the current one. This technique gets implicitly involved when savepoints are used and also when handling PL/pgSQL exceptions, as well as in some other, more exotic situations.

    => BEGIN;

    BEGIN

    => BEGIN;

    WARNING:  there is already a transaction in progress
    BEGIN

    => COMMIT;

    COMMIT

    => COMMIT;

    WARNING:  there is no transaction in progress
    COMMIT

### Errors and operation atomicity

What happens if an error occurs while the operation is being performed? For example, like this:

    => BEGIN;
    => SELECT * FROM t;

     id |  s  
    ----+-----
      2 | FOO
      4 | BAR
    (2 rows)

    => UPDATE t SET s = repeat('X', 1/(id-4));

    ERROR:  division by zero

An error occurred. Now the transaction is treated as aborted and no operations are permitted in it:

    => SELECT * FROM t;

    ERROR:  current transaction is aborted, commands ignored until end of transaction block

And even if we try to commit the changes, PostgreSQL will report the rollback:

    => COMMIT;

    ROLLBACK

Why is it impossible to continue execution of the transaction after a failure? The thing is that the error could occur so that we would get access to part of the changes, that is, the atomicity would be broken not only for the transaction, but even for a single operator. For instance, in our example the operator could have updated one row before the error occurred:

    => SELECT * FROM heap_page('t',0);

     ctid  | state  |   xmin   | xmax  | t_ctid 
    -------+--------+----------+-------+--------
     (0,1) | normal | 3669 (c) | 3672  | (0,4)
     (0,2) | normal | 3670 (a) | 0 (a) | (0,2)
     (0,3) | normal | 3671 (c) | 0 (a) | (0,3)
     (0,4) | normal | 3672     | 0 (a) | (0,4)
    (4 rows)

It's worth noting that psql has a mode that allows continuing the transaction after failure, as if the effects of the erroneous operator were rolled back.

    => \set ON_ERROR_ROLLBACK on
    => BEGIN;
    => SELECT * FROM t;

     id |  s  
    ----+-----
      2 | FOO
      4 | BAR
    (2 rows)

    => UPDATE t SET s = repeat('X', 1/(id-4));

    ERROR:  division by zero

    => SELECT * FROM t;

     id |  s  
    ----+-----
      2 | FOO
      4 | BAR
    (2 rows)

    => COMMIT;

It's easy to figure out that in this mode, psql actually establishes an implicit savepoint before each command and initiates a rollback to it in the event of failure. This mode is not used by default since establishing savepoints (even without a rollback to them) entails a significant overhead.
