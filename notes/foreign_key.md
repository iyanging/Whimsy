# Deliberately choose not to use foreign key constraints

> Ref: <https://seangoedecke.com/invalid-states/>
>
> 总结：
>
> * 使用外键约束，在删除父记录时必须删除所有相关记录；虽然也可以使用 `ON DELETE SET NULL`，但这只适用于字段可为空的情况，而可为空本身可能就是你的领域模型中的一个无效状态
> * 对于关系不那么牢固的情况又如何？如果一个 `post` 有一个 `reviewer_id`，当该审阅者的账户被删除时会发生什么？删除帖子似乎不合适
> * 对于需要做分片或者同步的表，如果它与其他表有任何外键关系，这会让事情从本质层面变得很麻烦，也许有成熟的方案，但对整个系统而言会引入额外的复杂度，甚至会以意想不到的方式影响数据库的正常使用，代价通常无法忽略

In a relational database, tables are related by primary key (typically ID): a `posts` table will have a `user_id` column to show which user owns which post, corresponding to the value of the `id` column in the `users` table. When you want to fetch the posts belonging to user 3, you’ll run SQL like `SELECT * FROM posts WHERE user_id = 3`.

> 在关系数据库中，表通过主键（通常是ID）相互关联：一个`posts`表会有一个`user_id`列，用来显示哪个用户拥有哪个帖子，该列对应于`users`表中的`id`列的值。当你想获取属于用户3的帖子时，你将运行类似 `SELECT * FROM posts WHERE user_id = 3` 的 SQL 查询。

A foreign key constraint forces `user_id` to correspond to an actual row in the `users` table. If you try to create or update a post with user_id 999, and there is no user with that id, the foreign key constraint will cause the SQL query to fail.

> 外键约束强制 `user_id` 对应 `users` 表中的实际行。如果您尝试创建或更新一个 user_id 为 999 的帖子，但不存在该 id 的用户，外键约束将导致 SQL 查询失败。

This sounds great, right? A record pointing at a non-existent user is in an invalid state. Shouldn’t we want it to be impossible to represent invalid states? However, many large tech companies - including the two I’ve worked for, GitHub and Zendesk - deliberately choose not to use foreign key constraints. Why not?

> 这听起来很棒，对吧？指向不存在用户的记录处于无效状态。我们难道不想让无效状态不可能被表示出来吗？然而，包括我曾工作过的 GitHub 和 Zendesk 在内的许多大型科技公司，都故意选择不使用外键约束。为什么不呢？

The main reason is flexibility[^2]. In practice, it’s much easier to deal with some illegal states in application logic (like posts with no user attached) than it is to deal with the constraint. With foreign key constraints, you have to delete all related records when a parent record is deleted (edit: I know you can `ON DELETE SET NULL` as well, but that only works if the field is nullable, which may itself be an invalid state in your domain model). That might be okay for users and posts - though it could become a very expensive operation - but what about relationships that are less solid? If a post has a `reviewer_id`, what happens when that reviewer’s account is deleted? It doesn’t seem right to delete the post, surely. And so on.

> 主要原因是灵活性[^2]。在实践中，处理应用程序逻辑中的一些非法状态（例如没有用户关联的帖子）比处理约束要容易得多。使用外键约束，在删除父记录时必须删除所有相关记录（编辑：我知道你也可以使用 `ON DELETE SET NULL`，但这只适用于字段可为空的情况，而可为空本身可能就是你的领域模型中的一个无效状态）。这对于用户和帖子来说可能还可以——尽管这可能是一项非常昂贵的操作——但对于关系不那么牢固的情况又如何呢？如果一个帖子有一个 `reviewer_id`，当该审阅者的账户被删除时会发生什么？删除帖子似乎不合适，肯定不是。等等。

If you want to change the database schema, foreign key constraints can be a big problem. Maybe you want to move a table to a different database cluster or shard. If it has any foreign key relationships to other tables, watch out! If you’re not also moving those tables over, you’ll have to remove the foreign key constraint then anyway. Even if you are moving those tables too, it’s a giant hassle to move the data in a way that’s compliant with the constraint, because you can’t just replicate a single table at a time - you have to move the data in chunks that keep the foreign key relationships intact.

> 如果你想更改数据库模式，外键约束可能会成为一个大问题。也许你想将一个表移动到不同的数据库集群或分片。如果它与其他表有任何外键关系，请注意！如果你不也移动那些表，你最终还是不得不删除外键约束。即使你也要移动那些表，以一种符合约束的方式移动数据也是一件非常麻烦的事情，因为你不能一次只复制一个表——你必须分块移动数据，以保持外键关系完好无损。

[^2]: Foreign key constraints also have performance issues at scale, make database migrations very difficult when you’re touching the foreign key column, and complicate common big-company patterns like soft-deletes.