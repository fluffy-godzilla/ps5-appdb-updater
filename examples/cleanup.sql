update tbl_iconinfo_YYYY SET visible = 0 where titleId in ('NPXS40037', 'NPXS40047', 'NPXS40056', 'NPXS40075');
update tbl_iconinfo_YYYY SET visible = 1 where titleId in ('NPXS40063', 'NPXS40071', 'NPXS40139');

update tbl_contentInfo SET sortPriority = 10 where titleId == 'NPXS40071';
update tbl_contentInfo SET sortPriority = 12 where titleId == 'NPXS40063';

update tbl_contentInfo SET sortPriority = 13 where titleId == 'NPXS40139';
update tbl_contentInfo SET sortPriority = 15 where titleId == 'PPSA01650';
