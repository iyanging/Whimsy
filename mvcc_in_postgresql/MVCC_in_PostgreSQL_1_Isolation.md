# MVCC in PostgreSQL — 1. Isolation

> Source: [MVCC in PostgreSQL — 1. Isolation](https://postgrespro.com/blog/pgsql/5967856)

With this article I start a series about the internal structure of PostgreSQL.

The material will be based on our training courses on database administration that Pavel Luzanov and I are creating. Not everyone likes to watch video (I definitely do not), and reading slides, even with comments, is no good at all.

We strongly recommend you to get familiar with our [2-Day Introduction to PostgreSQL 11](https://postgrespro.com/education/courses/2dINTRO).

Of course, the articles will not be exactly the same as the content of the courses. I will talk only about how everything is organized, omitting the administration itself, but I will try to do it in more detail and more thoroughly. And I believe that the knowledge like this is as useful to an application developer as it is to an administrator.

I will target those who already have some experience in using PostgreSQL and at least in general understand what is what. The text will be too difficult for beginners. For example, I will not say a word about how to install PostgreSQL and run psql.

The stuff in question does not vary much from version to version, but I will use PostgreSQL 11.

The first series deals with issues related to isolation and multiversion concurrency, and the plan of the series is as follows:

1. Isolation as understood by the standard and PostgreSQL (this article).
2. [Forks, files, pages](https://postgrespro.com/blog/pgsql/5967858) — what is happening at the physical level.
3. [Row versions](./MVCC_in_PostgreSQL_3_Row_Versions.md), virtual transactions and subtransactions.
4. [Data snapshots](https://postgrespro.com/blog/pgsql/5967899) and the visibility of row versions; the event horizon.
5. [In-page vacuum and HOT updates](https://postgrespro.com/blog/pgsql/5967910).
6. [Normal vacuum](https://postgrespro.com/blog/pgsql/5967918).
7. [Autovacuum](https://postgrespro.com/blog/pgsql/5967933).
8. Transaction id wraparound and freezing.

Off we go!

> And before we start, I would like to thank Elena Indrupskaya for translating the articles to English.

## What is isolation and why is it important?

Probably, everyone is at least aware of the existence of transactions, has come across the abbreviation ACID, and has heard about isolation levels. But we still happen to face the opinion that this pertains to theory, which is not necessary in practice. Therefore, I will spend some time trying to explain why this is really important.

You are unlikely to be happy if an application gets incorrect data from the database or if the application writes incorrect data to the database.

But what is “correct” data? It is known that _integrity constraints_, such as NOT NULL or UNIQUE, can be created at the database level. If the data always meet integrity constraints (and this is so since the DBMS guarantees it), then they are integral.

Are _correct_ and _integral_ the same things? Not exactly. Not all constraints can be specified at the database level. Some of the constraints are too complicated, for example, that cover several tables at once. And even if a constraint in general could have been defined in the database, but for some reason it was not, it does not mean that the constraint can be violated.

So, _correctness_ is stronger than _integrity_, but we do not know exactly what this means. We have nothing but admit that the “gold standard” of correctness is an application that, as we would like to believe, is written _correctly_ and never runs wrong. In any case, if an application does not violate the integrity, but violates the correctness, the DBMS will not know about it and will not catch the application “red-handed”.

Further we will use the term _consistency_ to refer to correctness.

Let us, however, assume that an application executes only correct sequences of operators. What is the role of DBMS if the application is correct as it is?

First, it turns out that a correct sequence of operators can temporarily break data consistency, and, oddly enough, this is normal. A hackneyed but clear example is a transfer of funds from one account to another. The consistency rule may sound like this: _a transfer never changes the total amount of money on the accounts_ (this rule is quite difficult to specify in SQL as an integrity constraint, so it exists at the application level and is invisible to the DBMS). A transfer consists of two operations: the first reduces the funds on one account, and the second one — increases them on the other. The first operation breaks data consistency, while the second one restores it.

> A good exercise is to implement the above rule at the level of integrity constraints.

What if the first operation is performed and the second is not? In fact, without much ado: during the second operation there may occur an electricity failure, a server crash, division by zero — whatever. It is clear that the consistency will be broken, and this cannot be permitted. In general, it is possible to resolve such issues at the application level, but at the cost of tremendous efforts; however, fortunately, it is not necessary: this is done by the DBMS. But to do this, the DBMS must know that the two operations are an indivisible whole. That is, _a transaction_.

It turns out interesting: as the DBMS knows that operations make up a transaction, it helps maintain consistency by ensuring that the transactions are atomic, and it does this without knowing anything about specific consistency rules.

But there is a second, more subtle point. As soon as several simultaneous transactions appear in the system, which are absolutely correct separately, they may fail to work correctly together. This is because the order of operations is mixed up: you cannot assume that all the operations of one transaction are performed first, and then all the operations of the other one.

A note about simultaneity. Indeed, transactions can run simultaneously on a system with a multi-core processor, disk array, etc. But the same reasoning holds for a server that executes commands sequentially, in a time-sharing mode: during certain clock cycles one transaction is executed, and during next certain cycles the other one is. Sometimes the term _concurrent_ execution is used for a generalization.

Situations when correct transactions work together incorrectly are called _anomalies_ of concurrent execution.

For a simple example: if an application wants to get correct data from the database, it must not, at least, see changes of other uncommitted transactions. Otherwise, you can not only get inconsistent data, but also see something that has never been in the database (if the transaction is canceled). This anomaly is called a _dirty read_.

There are other, more complex, anomalies, which we will deal with a bit later.

It is certainly impossible to avoid concurrent execution: otherwise, what kind of performance can we talk of? But you cannot either work with incorrect data.

And again the DBMS comes to the rescue. You can make transactions executed _as if_ sequentially, _as if_ one after another. In other words — _isolated_ from one another. In reality, the DBMS can perform operations mixed up, but ensure that the result of a concurrent execution will be the same as the result of some of the possible sequential executions. And this eliminates any possible anomalies.

So we arrived at the definition:

> A transaction is a set of operations performed by an application that transfers a database from one correct state to another correct state (consistency), provided that the transaction is completed (atomicity) and without interference from other transactions (isolation).

This definition unites the first three letters of the acronym ACID. They are so closely related with one another that it makes no sense to consider one without the others. In fact, it is also difficult to detach the letter D (durability). Indeed, when a system crashes, it still has changes of uncommitted transactions, with which you need to do something to restore data consistency.

Everything would have been fine, but the implementation of complete isolation is a technically difficult task entailing a reduction in system throughput. Therefore, in practice very often (not always, but almost always) the weakened isolation is used, which prevents some, but not all anomalies. This means that a part of the work to ensure data correctness falls on the application. For this very reason it is very important to understand which level of isolation is used in the system, what guarantees it gives and what it does not, and how to write correct code under such conditions.

## Isolation levels and anomalies in SQL standard

The SQL standard has long described four levels of isolation. These levels are defined by listing anomalies that are allowed or not allowed when transactions are executed simultaneously at this level. Therefore, to talk about these levels, it is necessary to get to know the anomalies.

I emphasize that in this part we are talking about the standard, that is, about a theory, on which practice is significantly based, but from which at the same time it considerably diverges. Therefore, all the examples here are speculative. They will use the same operations on customer accounts: this is quite demonstrative, although, admittedly, has nothing to do with how bank operations are organized in reality.

### Lost update

Let's start with _lost update_. This anomaly occurs when two transactions read the same row of the table, then one transaction updates that row, and then the second transaction also updates the same row without taking into account the changes made by the first transaction.

For example, two transactions are going to increase the amount on the same account by ₽100 (₽ is the currency sign for Russian rouble). The first transaction reads the current value (₽1000) and then the second transaction reads the same value. The first transaction increases the amount (this gives ₽1100) and writes this value. The second transaction acts the same way: it gets the same ₽1100 and writes this value. As a result, the customer lost ₽100.

The standard does not allow lost updates at any isolation level.

### Dirty read and Read Uncommitted

A _dirty read_ is what we have already got acquainted with. This anomaly occurs when a transaction reads changes that have not been committed yet by another transaction.

For example, the first transaction transfers all the money from the customer's account to another account, but does not commit the change. Another transaction reads the account balance, to get ₽0, and refuses to withdraw cash to the customer, although the first transaction aborts and reverts its changes, so the value of 0 has never existed in the database.

The standard allows dirty reads at the Read Uncommitted level.

### Non-repeatable read and Read Committed

A _non-repeatable read_ anomaly occurs when a transaction reads the same row twice, and in between the reads, the second transaction modifies (or deletes) that row and commits the changes. Then the first transaction will get different results.

For example, let a consistency rule _forbid negative amounts on customer accounts_. The first transaction is going to reduce the amount on the account by ₽100\. It checks the current value, gets ₽1000 and decides that the decrease is possible. At the same time the second transaction reduces the amount on the account to zero and commits the changes. If the first transaction now rechecked the amount, it would get ₽0 (but it has already decided to reduce the value, and the account “goes into the red”).

The standard allows non-repeatable reads at the Read Uncommitted and Read Committed levels. But Read Committed does not allow dirty reads.

### Phantom read and Repeatable Read

A _phantom read_ occurs when a transaction reads a set of rows by the same condition twice, and in between the reads, the second transaction adds rows that meet that condition (and commits the changes). Then the first transaction will get a different sets of rows.

For example, let a consistency rule _prevent a customer from having more than 3 accounts_. The first transaction is going to open a new account, checks the current number of accounts (say, 2), and decides that opening is possible. At the same time, the second transaction also opens a new account for the customer and commits the changes. Now if the first transaction rechecked the number, it would get 3 (but it is already opening another account, and the customer appears to have 4 of them).

The standard allows phantom reads at the Read Uncommitted, Read Committed, and Repeatable Read levels. However, non-repeatable read is not allowed at the Repeatable Read level.

### The absence of anomalies and Serializable

The standard defines one more level — Serializable — which does not allow any anomalies. And this is not the same as to forbid lost updates and dirty, non-repeatable, or phantom reads.

The thing is that there are much more known anomalies than listed in the standard and also an unknown number of yet unknown ones.

The Serializable level must prevent _absolutely all_ anomalies. It means that at this level, an application developer does not need to think about concurrent execution. If transactions perform a correct sequence of operators working separately, the data will be consistent also when these transactions are executed simultaneously.

### Summary table

Now we can provide a well-known table. But here the last column, which is missing from the standard, is added for clarity.

<table>

<tbody>

<tr>

<th> </th>

<th>Lost changes</th>

<th>Dirty read</th>

<th>Non-repeatable read</th>

<th>Phantom read</th>

<th>Other anomalies</th>

</tr>

<tr>

<th>Read Uncommitted</th>

<th>—</th>

<th>Yes</th>

<th>Yes</th>

<th>Yes</th>

<th>Yes</th>

</tr>

<tr>

<th>Read Committed</th>

<th>—</th>

<th>—</th>

<th>Yes</th>

<th>Yes</th>

<th>Yes</th>

</tr>

<tr>

<th>Repeatable Read</th>

<th>—</th>

<th>—</th>

<th>—</th>

<th>Yes</th>

<th>Yes</th>

</tr>

<tr>

<th>Serializable</th>

<th>—</th>

<th>—</th>

<th>—</th>

<th>—</th>

<th>—</th>

</tr>

</tbody>

</table>

### Why exactly these anomalies?

Why does the standard list only a few of the many possible anomalies, and why are they exactly these?

No one seems to know it for sure. But here the practice is evidently ahead of the theory, so it is possible that at that time (of the SQL:92 standard) other anomalies were not just thought of.

In addition, it was assumed that the isolation must be built on locks. The idea behind the widely used _Two-Phase Locking protocol_ (2PL) is that during execution, a transaction locks the rows it is working with and releases the locks on completion. Considerably simplifying, the more locks a transaction acquires, the better it is isolated from other transactions. But the performance of the system also suffers more, because instead of working together, transactions begin to queue for the same rows.

My sense is that it's just the number of locks required, which accounts for the difference between the isolation levels of the standard.

If a transaction locks the rows to be modified from updating, but not from reading, we get the Read Uncommitted level: lost changes are not allowed, but uncommitted data can be read.

If a transaction locks the rows to be modified from both reading and updating, we get the Read Committed level: you cannot read uncommitted data, but you can get a different value (non-repeatable read) when you access the row again.

If a transaction locks the rows both to be read and to be modified and both from reading and updating, we get the Repeatable Read level: re-reading the row will return the same value.

But there is an issue with Serializable: you cannot lock a row that does not exist yet. Therefore, a phantom read is still possible: another transaction may add (but not delete) a row that meets the conditions of a previously executed query, and that row will be included in the re-selection.

Therefore, to implement the Serializable level, normal locks do not suffice — you need to lock conditions (predicates) rather than rows. Therefore, such locks were called _predicate_. They were proposed in 1976, but their practical applicability is limited by fairly simple conditions for which it is clear how to join two different predicates. As far as I know, such locks have never been implemented in any system so far.

## Isolation levels in PostgreSQL

Over time, lock-based protocols of transaction management were replaced with the Snapshot Isolation protocol (SI). Its idea is that each transaction works with a consistent snapshot of the data at a certain point in time, and only those changes get into the snapshot that were committed before it was created.

This isolation automatically prevents dirty reads. Formally, you can specify the Read Uncommitted level in PostgreSQL, but it will work exactly the same way as Read Committed. Therefore, further we will not talk about the Read Uncommitted level at all.

PostgreSQL implements a _multiversion_ variant of this protocol. The idea of multiversion concurrency is that multiple versions of the same row can coexist in a DBMS. This allows you to build a snapshot of the data using existing versions and to use a minimum of locks. Actually, only subsequent changes to the same row are locked. All other operations are performed simultaneously: write transactions never lock read-only transactions, and read-only transactions never lock anything.

By using data snapshots, isolation in PostgreSQL is stricter than required by the standard: the Repeatable Read level does not allow not only non-repeatable reads, but also phantom reads (although it does not provide complete isolation). And this is achieved without loss of efficiency.

<table>

<tbody>

<tr>

<th> </th>

<th>Lost changes</th>

<th>Dirty read</th>

<th>Non-repeatable read</th>

<th>Phantom read</th>

<th>Other anomalies</th>

</tr>

<tr>

<th>Read Uncommitted</th>

<th>—</th>

<th>—</th>

<th>Yes</th>

<th>Yes</th>

<th>Yes</th>

</tr>

<tr>

<th>Read Committed</th>

<th>—</th>

<th>—</th>

<th>Yes</th>

<th>Yes</th>

<th>Yes</th>

</tr>

<tr>

<th>Repeatable Read</th>

<th>—</th>

<th>—</th>

<th>—</th>

<th>—</th>

<th>Yes</th>

</tr>

<tr>

<th>Serializable</th>

<th>—</th>

<th>—</th>

<th>—</th>

<th>—</th>

<th>—</th>

</tr>

</tbody>

</table>

We will talk in the next articles of how multiversion concurrency is implemented “under the hood,” and now we will look in detail at each of the three levels with a user's eye (as you know, the most interesting is hidden behind “other anomalies”). To do this, let's create a table of accounts. Alice and Bob have ₽1000 each, but Bob has two opened accounts:

    => CREATE TABLE accounts(
      id integer PRIMARY KEY GENERATED BY DEFAULT AS IDENTITY,
      number text UNIQUE,
      client text,
      amount numeric
    );
    => INSERT INTO accounts VALUES
      (1, '1001', 'alice', 1000.00),
      (2, '2001', 'bob', 100.00),
      (3, '2002', 'bob', 900.00);

### Read Committed

#### The absence of dirty read

It is easy to make sure that dirty data cannot be read. We start the transaction. By default it will use the Read Committed isolation level:

    => BEGIN;
    => SHOW transaction_isolation;

     transaction_isolation 
    -----------------------
     read committed
    (1 row)

More precisely, the default level is set by the parameter, which can be changed if necessary:

    => SHOW default_transaction_isolation;

     default_transaction_isolation 
    -------------------------------
     read committed
    (1 row)

So, in an open transaction, we withdraw funds from the account, but do not commit the changes. The transaction sees its own changes:

    => UPDATE accounts SET amount = amount - 200 WHERE id = 1;
    => SELECT * FROM accounts WHERE client = 'alice';

     id | number | client | amount 
    ----+--------+--------+--------
      1 | 1001   | alice  | 800.00
    (1 row)

In the second session, we will start another transaction with the same Read Committed level. To distinguish between the transactions, commands of the second transaction will be indented and marked with a bar.

In order to repeat the above commands (which is useful), you need to open two terminals and run psql in each one. In the first terminal, you can enter the commands of one transaction, and in the second one — those of the other.

    |  => BEGIN;
    |  => SELECT * FROM accounts WHERE client = 'alice';

    |   id | number | client | amount 
    |  ----+--------+--------+---------
    |    1 | 1001   | alice  | 1000.00
    | (1 row)

As expected, the other transaction does not see uncommitted changes since dirty reads are not allowed.

#### Non-repeatable read

Now let the first transaction commit the changes and the second one re-execute the same query.

    => COMMIT;

    | => SELECT * FROM accounts WHERE client = 'alice';

    |   id | number | client | amount 
    |  ----+--------+--------+--------
    |    1 | 1001   | alice  | 800.00
    | (1 row)

    |  => COMMIT;

The query already gets new data — and this is the _non-repeatable read_ anomaly, which is allowed at the Read Committed level.

_Practical conclusion_: in a transaction, you cannot make decisions based on data read by a previous operator because things can change between execution of the operators. Here is an example whose variations occur so often in application code that it is considered a classic antipattern:

          IF (SELECT amount FROM accounts WHERE id = 1) >= 1000 THEN
            UPDATE accounts SET amount = amount - 1000 WHERE id = 1;
          END IF;

During the time that passes between checking and updating, other transactions can change the state of the account any way, so such a “check” secures from nothing. It is convenient to imagine that between operators of one transaction any other operators of other transactions can “wedge,” for example, as follows:

          IF (SELECT amount FROM accounts WHERE id = 1) >= 1000 THEN
           -----
          |   UPDATE accounts SET amount = amount - 200 WHERE id = 1;
          |   COMMIT;
           -----
            UPDATE accounts SET amount = amount - 1000 WHERE id = 1;
          END IF;

If everything can be spoiled by rearranging the operators, then the code is written incorrectly. And do not deceive yourself that such a coincidence will not happen — it will, for sure.

But how to write code correctly? The options tend to be as follows:

*   Not to write code.

    This is not a joke. For example, in this case, checking easily turns into an integrity constraint:

    `ALTER TABLE accounts ADD CHECK amount >= 0;`

    No checks are needed now: simply perform the operation and, if necessary, handle the exception that will occur if an integrity violation is attempted.

*   To use a single SQL statement.

    Consistency problems arise since in the time interval between operators another transaction can complete, which will change the visible data. And if there is one operator, then there are no time intervals.

    PostgreSQL has enough techniques to solve complex problems with one SQL statement. Let's note common table expressions (CTE), in which, among the rest, you can use INSERT/UPDATE/DELETE statements, as well as the INSERT ON CONFLICT statement, which implements the logic of “insert, but if the row already exists, update” in one statement.

*   Custom locks.

    The last resort is to manually set an exclusive lock on all the necessary rows (SELECT FOR UPDATE) or even on the entire table (LOCK TABLE). This always works, but nullifies the benefits of multiversion concurrency: some operations will be executed sequentially instead of concurrent execution.

#### Inconsistent read

Before proceeding to the next level of isolation, you have to admit that it's not all as simple as it sounds. The implementation of PostgreSQL is such that it allows for other, less known, anomalies that are not regulated by the standard.

Let's assume that the first transaction started funds transfer from one Bob's account to the other:

    => BEGIN;
    => UPDATE accounts SET amount = amount - 100 WHERE id = 2;

At the same time, another transaction counts Bob's balance, and the calculation is performed in a loop over all Bob's accounts. In fact, the transaction starts with the first account (and, obviously, sees the previous state):

    |  => BEGIN;
    |  => SELECT amount FROM accounts WHERE id = 2;

    |   amount 
    |  --------
    |   100.00
    |  (1 row)

At this point in time, the first transaction completes successfully:

    => UPDATE accounts SET amount = amount + 100 WHERE id = 3;
    => COMMIT;

And the other one reads the state of the second account (and already sees the new value):

    |  => SELECT amount FROM accounts WHERE id = 3;

    |   amount 
    |  ---------
    |   1000.00
    |  (1 row)

    |  => COMMIT;

Therefore, the second transaction got ₽1100 in total, that is, incorrect data. And this is an _inconsistent read_ anomaly.

How to avoid such an anomaly while staying at the Read Committed level? Of course, use one operator. For example:

          SELECT sum(amount) FROM accounts WHERE client = 'bob';

Up to here I asserted that data visibility could only change between operators, but is that so obvious? And if the query takes long, can it see a part of the data in one state and a part in another one?

Let's check. A convenient way to do this is to insert a forced delay into the operator by calling the pg_sleep function. Its parameter specifies the delay time in seconds.

    => SELECT amount, pg_sleep(2) FROM accounts WHERE client = 'bob';

While this operator is executed, we transfer the funds back in another transaction:

    |  => BEGIN;
    |  => UPDATE accounts SET amount = amount + 100 WHERE id = 2;
    |  => UPDATE accounts SET amount = amount - 100 WHERE id = 3;
    |  => COMMIT;

The result shows that the operator sees the data in the state that they had at the time when execution of the operator started. This is undoubtedly correct.

     amount  | pg_sleep 
    ---------+----------
        0.00 | 
     1000.00 | 
    (2 rows)

But it's not that simple here either. PostgreSQL allows you to define functions, and functions have the concept of a _volatility category_. If a VOLATILE function is called in a query and another query is executed in that function, the query inside the function will see data that are inconsistent with the data in the main query.

    => CREATE FUNCTION get_amount(id integer) RETURNS numeric AS $
      SELECT amount FROM accounts a WHERE a.id = get_amount.id;
    $ VOLATILE LANGUAGE sql;

    => SELECT get_amount(id), pg_sleep(2)
    FROM accounts WHERE client = 'bob';

    |  => BEGIN;
    |  => UPDATE accounts SET amount = amount + 100 WHERE id = 2;
    |  => UPDATE accounts SET amount = amount - 100 WHERE id = 3;
    |  => COMMIT;

In this case, we get incorrect data — ₽100 are lost:

     get_amount | pg_sleep 
    ------------+----------
         100.00 | 
         800.00 | 
    (2 rows)

I emphasize that this effect is possible only at the Read Committed isolation level and only with the VOLATILE functions. The trouble is that by default, exactly this isolation level and this volatility category are used. Don't fall into thе trap!

#### Inconsistent read in exchange for lost changes

We can also get an inconsistent read within a single operator during an update, although in a somewhat unexpected way.

Let's see what happens when two transactions try to modify the same row. Now Bob has ₽1000 on two accounts:

    => SELECT * FROM accounts WHERE client = 'bob';

     id | number | client | amount 
    ----+--------+--------+--------
      2 | 2001   | bob    | 200.00
      3 | 2002   | bob    | 800.00
    (2 rows)

We start a transaction that reduces Bob's balance:

    => BEGIN;
    => UPDATE accounts SET amount = amount - 100 WHERE id = 3;

At the same time, in another transaction interest accrues on all customer accounts with the total balance equal to or greater than ₽1000:

    |  => UPDATE accounts SET amount = amount * 1.01
    |  WHERE client IN (
    |    SELECT client
    |    FROM accounts
    |    GROUP BY client
    |    HAVING sum(amount) >= 1000
    |  );

Execution of the UPDATE operator consists of two parts. First, actually SELECT is executed, which selects the rows to update that meet the appropriate condition. Because the change in the first transaction is not committed, the second transaction cannot see it, and the change does not affect the selection of rows for interest accrual. Well then, Bob's accounts meet the condition and once the update is executed, his balance should increase by ₽10.

The second stage of the execution is updating the selected rows one by one. Here the second transaction is forced to “hang” because the row with id = 3 is already locked by the first transaction.

Meanwhile, the first transaction commits the changes:

    => COMMIT;

What will the result be?

    => SELECT * FROM accounts WHERE client = 'bob';

     id | number | client | amount 
    ----+--------+--------+----------
      2 | 2001   | bob    | 202.0000
      3 | 2002   | bob    | 707.0000
    (2 rows)

Well, on one hand, the UPDATE command should not see the changes of the second transaction. But on the other hand, it should not lose the changes committed in the second transaction.

Once the lock is released, UPDATE re-reads the row it is trying to update (but only this one). As a result, Bob accrued ₽9, based on the amount of ₽900\. But if Bob had ₽900, his accounts should not have been in the selection at all.

So, the transaction gets incorrect data: some of the rows are visible at one point in time, and some at another one. Instead of a lost update we again get the anomaly of _inconsistent read_.

> Attentive readers note that with some help from the application you can get a lost update even at the level of Read Committed. For example:
> 
>     
>           x := (SELECT amount FROM accounts WHERE id = 1);
>           UPDATE accounts SET amount = x + 100 WHERE id = 1;
>     
> 
> The database is not to blame: it gets two SQL statements and knows nothing about the fact that the value of x + 100 is somehow related to accounts amount. Avoid writing code that way.

### Repeatable Read

#### The absence of non-repeatable and phantom reads

The very name of the isolation level assumes that reading is repeatable. Let's check it, and at the same time make sure there are no phantom reads. To do this, in the first transaction, we revert Bob's accounts to their previous state and create a new account for Charlie:

    => BEGIN;
    => UPDATE accounts SET amount = 200.00 WHERE id = 2;
    => UPDATE accounts SET amount = 800.00 WHERE id = 3;
    => INSERT INTO accounts VALUES
      (4, '3001', 'charlie', 100.00);
    => SELECT * FROM accounts ORDER BY id;

     id | number | client | amount 
    ----+--------+---------+--------
      1 | 1001   | alice   | 800.00
      2 | 2001   | bob     | 200.00
      3 | 2002   | bob     | 800.00
      4 | 3001   | charlie | 100.00
    (4 rows)

In the second session, we start the transaction with the Repeatable Read level by specifying it in the BEGIN command (the level of the first transaction is inessential).

    |  => BEGIN ISOLATION LEVEL REPEATABLE READ;
    |  => SELECT * FROM accounts ORDER BY id;

    |   id | number | client | amount 
    |  ----+--------+--------+----------
    |    1 | 1001   | alice  |   800.00
    |    2 | 2001   | bob    | 202.0000
    |    3 | 2002   | bob    | 707.0000
    |  (3 rows)

Now the first transaction commits the changes and the second re-executes the same query.

    => COMMIT;

    | => SELECT * FROM accounts ORDER BY id;

    |   id | number | client | amount 
    |  ----+--------+--------+----------
    |    1 | 1001   | alice  | 800.00
    |    2 | 2001   | bob    | 202.0000
    |    3 | 2002   | bob    | 707.0000
    |  (3 rows)

    |  => COMMIT;

The second transaction still sees exactly the same data as at the beginning: no changes to existing rows or new rows are visible.

At this level, you can avoid worrying about something that may change between two operators.

#### Serialization error in exchange for lost changes

We've discussed earlier that when two transactions update the same row at the Read Committed level, an anomaly of inconsistent read may occur. This is because the waiting transaction re-reads the locked row and therefore does not see it as of the same point in time as the other rows.

At the Repeatable Read level, this anomaly is not allowed, but if it does occur, nothing can be done — so the transaction terminates with a serialization error. Let's check it by repeating the same scenario with interest accrual:

    => SELECT * FROM accounts WHERE client = 'bob';

     id | number | client | amount 
    ----+--------+--------+--------
      2 | 2001 | bob | 200.00
      3 | 2002 | bob | 800.00
    (2 rows)

    => BEGIN;
    => UPDATE accounts SET amount = amount - 100.00 WHERE id = 3;

    |  => BEGIN ISOLATION LEVEL REPEATABLE READ;
    |  => UPDATE accounts SET amount = amount * 1.01
    |  WHERE client IN (
    |    SELECT client
    |    FROM accounts
    |    GROUP BY client
    |    HAVING sum(amount) >= 1000
    |  );

    => COMMIT;

    |  ERROR: could not serialize access due to concurrent update

    |  => ROLLBACK;

The data remained consistent:

    => SELECT * FROM accounts WHERE client = 'bob';

     id | number | client | amount 
    ----+--------+--------+--------
      2 | 2001   | bob    | 200.00
      3 | 2002   | bob    | 700.00
    (2 rows)

The same error will occur in the case of any other competitive change of a row, even if the columns of our concern were not actually changed.

_Practical conclusion_: if your application uses the Repeatable Read isolation level for write transactions, it must be ready to repeat transactions that terminated with a serialization error. For read-only transactions, this outcome is not possible.

#### Inconsistent write (write skew)

So, in PostgreSQL, at the Repeatable Read isolation level, all anomalies described in the standard are prevented. But not all anomalies in general. It turns out there are _exactly two_ anomalies that are still possible. (This is true not only for PostgreSQL, but also for other implementations of Snapshot Isolation.)

The first of these anomalies is an _inconsistent write_.

Let the following consistency rule holds: _negative amounts on customer accounts are allowed if the total amount on all accounts of that customer remains non-negative_.

The first transaction gets the amount on Bob's accounts: ₽900.

    => BEGIN ISOLATION LEVEL REPEATABLE READ;
    => SELECT sum(amount) FROM accounts WHERE client = 'bob';

      sum 
    --------
     900.00
    (1 row)

The second transaction gets the same amount.

    |  => BEGIN ISOLATION LEVEL REPEATABLE READ;
    |  => SELECT sum(amount) FROM accounts WHERE client = 'bob';

    |    sum 
    |  --------
    |   900.00
    | (1 row)

The first transaction rightfully believes that the amount of one of the accounts can be reduced by ₽600.

    => UPDATE accounts SET amount = amount - 600.00 WHERE id = 2;

And the second transaction comes to the same conclusion. But it reduces another account:

    |  => UPDATE accounts SET amount = amount - 600.00 WHERE id = 3;
    |  => COMMIT;

    => COMMIT;
    => SELECT * FROM accounts WHERE client = 'bob';

     id | number | client | amount 
    ----+--------+--------+---------
      2 | 2001   | bob    | -400.00
      3 | 2002   | bob    | 100.00
    (2 rows)

We managed to make Bob's balance go into the red, although each transaction is working correctly alone.

#### Read-only transaction anomaly

This is the second and last of the anomalies that are possible at the Repeatable Read level. To demonstrate it, you will need three transactions, two of which will change the data, and the third will only read it.

But first let's restore the state of Bob's accounts:

    => UPDATE accounts SET amount = 900.00 WHERE id = 2;
    => SELECT * FROM accounts WHERE client = 'bob';

     id | number | client | amount
    ----+--------+--------+--------
      3 | 2002   | bob    | 100.00
      2 | 2001   | bob    | 900.00
    (2 rows)

In the first transaction, interest on the amount available on all Bob's accounts accrues. Interest is credited to one of his accounts:

    => BEGIN ISOLATION LEVEL REPEATABLE READ; -- 1
    => UPDATE accounts SET amount = amount + (
      SELECT sum(amount) FROM accounts WHERE client = 'bob'
    ) * 0.01
    WHERE id = 2;

Then another transaction withdraws money from another Bob's account and commits its changes:

    |  => BEGIN ISOLATION LEVEL REPEATABLE READ; -- 2
    |  => UPDATE accounts SET amount = amount - 100.00 WHERE id = 3;
    |  => COMMIT;

If the first transaction is committed at this point, no anomaly will occur: we could assume that the first transaction was executed first and then the second (but not vice versa because the first transaction saw the state of the account with id = 3 before that account was changed by the second transaction).

But imagine that at this point the third (read-only) transaction begins, which reads the state of some account that is not affected by the first two transactions:

    |  => BEGIN ISOLATION LEVEL REPEATABLE READ; -- 3
    |  => SELECT * FROM accounts WHERE client = 'alice';

    |   id | number | client | amount 
    |  ----+--------+--------+--------
    |    1 | 1001   | alice  | 800.00
    |  (1 row)

And only after that the first transaction is completed:

    => COMMIT;

What state should the third transaction see now?

    |    SELECT * FROM accounts WHERE client = ‘bob’;

Once started, the third transaction could see the changes of the second transaction (which had already been committed), but not of the first (which had not been committed yet). On the other hand, we have already ascertained above that the second transaction should be considered started after the first one. Whatever state the third transaction sees will be inconsistent — this is just the anomaly of a read-only transaction. But at the Repeatable Read level it is allowed:

    |    id | number | client | amount
    |   ----+--------+--------+--------
    |     2 | 2001   | bob    | 900.00
    |     3 | 2002   | bob    | 0.00
    |   (2 rows)

    |   => COMMIT;

### Serializable

The Serializable level prevents all anomalies possible. In fact, Serializable is built on top of the Snapshot Isolation. Those anomalies that do not occur with Repeatable Read (such as a dirty, non-repeatable, or phantom read) do not occur at the Serializable level either. And those anomalies that occur (an inconsistent write and a read-only transaction anomaly) are detected, and the transaction aborts — a familiar serialization error occurs: _could not serialize access_.

#### Inconsistent write (write skew)

To illustrate this, let's repeat the scenario with an inconsistent write anomaly:

    => BEGIN ISOLATION LEVEL SERIALIZABLE;
    => SELECT sum(amount) FROM accounts WHERE client = 'bob';

       sum 
    ----------
     910.0000
    (1 row)

    |   => BEGIN ISOLATION LEVEL SERIALIZABLE;
    |   => SELECT sum(amount) FROM accounts WHERE client = 'bob';

    |      sum 
    |   ----------
    |    910.0000
    |   (1 row)

    => UPDATE accounts SET amount = amount - 600.00 WHERE id = 2;

    |   => UPDATE accounts SET amount = amount - 600.00 WHERE id = 3;
    |   => COMMIT;

    => COMMIT;

    ERROR:  could not serialize access due to read/write dependencies among transactions
    DETAIL:  Reason code: Canceled on identification as a pivot, during commit attempt.
    HINT:  The transaction might succeed if retried.

Just like at the Repeatable Read level, an application that uses the Serializable isolation level must repeat transactions that terminated with a serialization error, as the error message prompts us.

We gain simplicity of programming, but the price for that is a forced termination of some fraction of transactions and a need to repeat them. The question, of course, is how large this fraction is. If only those transactions terminated that do incompatibly overlap with other transactions, it would have been nice. But such an implementation would inevitably be resource-intensive and inefficient because you would have to track the operations on each row.

Actually, the implementation of PostgreSQL is such that it allows false negatives: some absolutely normal transactions that are just “unlucky” will also abort. As we will see later, this depends on many factors, such as the availability of appropriate indexes or the amount of RAM available. In addition, there are some other implementation restrictions, for example, queries at the Serializable level will not work on replicas. Although the work on improving the implementation continues, the existing limitations make this level of isolation less attractive.

#### Read-only transaction anomaly

For a read-only transaction not to result in an anomaly and not to suffer from it, PostgreSQL offers an interesting technique: such a transaction can be locked until its execution is secure. This is the only case when a SELECT operator can be locked by row updates. This is what this looks like:

    => UPDATE accounts SET amount = 900.00 WHERE id = 2;
    => UPDATE accounts SET amount = 100.00 WHERE id = 3;
    => SELECT * FROM accounts WHERE client = 'bob' ORDER BY id;

     id | number | client | amount 
    ----+--------+--------+--------
      2 | 2001   | bob    | 900.00
      3 | 2002   | bob    | 100.00
    (2 rows)

    => BEGIN ISOLATION LEVEL SERIALIZABLE; -- 1
    => UPDATE accounts SET amount = amount + (
      SELECT sum(amount) FROM accounts WHERE client = 'bob'
    ) * 0.01
    WHERE id = 2;

    |  => BEGIN ISOLATION LEVEL SERIALIZABLE; -- 2
    |  => UPDATE accounts SET amount = amount - 100.00 WHERE id = 3;
    |  => COMMIT;

The third transaction is explicitly declared READ ONLY and DEFERRABLE:

    |   => BEGIN ISOLATION LEVEL SERIALIZABLE READ ONLY DEFERRABLE; -- 3
    |   => SELECT * FROM accounts WHERE client = 'alice';

When trying to execute the query, the transaction is locked because otherwise it would cause an anomaly.

    => COMMIT;

And only after the first transaction is committed, the third one continues execution:

    |    id | number | client | amount
    |   ----+--------+--------+--------
    |     1 | 1001   | alice  | 800.00
    |   (1 row)

    |   => SELECT * FROM accounts WHERE client = 'bob';

    |    id | number | client | amount 
    |   ----+--------+--------+----------
    |     2 | 2001   | bob    | 910.0000
    |     3 | 2002   | bob    | 0.00
    |   (2 rows)

    |   => COMMIT;

Another important note: if Serializable isolation is used, all transactions in the application must use this level. You cannot mix Read-Committed (or Repeatable Read) transactions with Serializable. That is, you _can_ mix, but then Serializable will behave like Repeatable Read without any warnings. We will discuss why this happens later, when we talk about the implementation.

So if you decide to use Serializble, it is best to globally set the default level (although this, of course, will not prevent you from specifying an incorrect level explicitly):

    ALTER SYSTEM SET default_transaction_isolation = 'serializable';

> You can find a more rigorous presentation of the issues related to transactions, consistency and anomalies in the [book](https://postgrespro.ru/education/books/dbtech) and [lecture course](https://postgrespro.ru/education/university/dbtech) by Boris Novikov “Fundamentals of database technologies” (available in Russion only).

## What isolation level to use?

The Read Committed isolation level is used by default in PostgreSQL, and it is likely that this level is used in the vast majority of applications. This default is convenient because at this level a transaction abort is possible only in case of failure, but not as a means to prevent inconsistency. In other words, a serialization error cannot occur.

The other side of the coin is a large number of possible anomalies, which have been discussed in detail above. The software engineer always has to keep them in mind and write code so as not to allow them to appear. If you cannot code the necessary actions in a single SQL statement, you have to resort to explicit locking. The most troublesome is that code is difficult to test for errors associated with getting inconsistent data, and the errors themselves can occur in unpredictable and non-reproducible ways and are therefore difficult to fix.

The Repeatable Read isolation level eliminates some of the inconsistency problems, but alas, not all. Therefore, you must not only remember about the remaining anomalies, but also modify the application so that it correctly handles serialization errors. It is certainly inconvenient. But for read-only transactions, this level perfectly complements Read Committed and is very convenient, for example, for building reports that use multiple SQL queries.

Finally, the Serializable level allows you not to worry about inconsistency at all, which greatly facilitates coding. The only thing that is required of the application is to be able to repeat any transaction when getting a serialization error. But the fraction of aborted transactions, additional overhead, and the inability to parallelize queries can significantly reduce the system throughput. Also note that the Serializable level is not applicable on replicas, and that it cannot be mixed with other isolation levels.
