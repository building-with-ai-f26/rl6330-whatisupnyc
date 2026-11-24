-- The event a Muse post became, so a corrected resend replaces it instead of leaving the old one listed.
alter table intake_events add column event_id bigint references events(id) on delete set null;
