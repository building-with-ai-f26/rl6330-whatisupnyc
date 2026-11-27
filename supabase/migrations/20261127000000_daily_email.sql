-- The daily email: the hour (New York time) each reader wants their list, whether they want it at all,
-- and the New York day it last went out, so nobody gets two in a day.
alter table profiles
    add column send_hour smallint not null default 8 check (send_hour in (7, 8, 9, 12, 17)),
    add column email_on boolean not null default false,
    add column emailed_on date;

-- Readers from before the daily email never asked for it, so theirs starts off (the default above fills
-- their rows with false); everyone who signs up from now on chooses a time and gets it.
alter table profiles alter column email_on set default true;

-- Readers may change when it comes and whether it comes. emailed_on stays server-only.
grant update (send_hour, email_on) on profiles to authenticated;
