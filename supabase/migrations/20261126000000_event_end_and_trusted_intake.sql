-- Events that run for several days (markets, sample sales) stay on lists until they end.
alter table events add column ends_at timestamptz;

-- Muse is now a trusted feed: it sends the end date and picture itself, and the collector no
-- longer fetches the event's page.
alter table intake_events add column ends_at timestamptz, add column image_url text;
