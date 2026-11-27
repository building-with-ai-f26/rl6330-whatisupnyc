-- The daily email: the hour (New York time) each reader wants their list, whether they want it at all,
-- and the New York day it last went out, so nobody gets two in a day.
alter table profiles
    add column send_hour smallint not null default 8 check (send_hour in (7, 8, 9, 12, 17)),
    add column email_on boolean not null default true,
    add column emailed_on date;

-- Readers may change when it comes and whether it comes. emailed_on stays server-only.
grant update (send_hour, email_on) on profiles to authenticated;
