-- Event images, and what readers save for later and rate.

-- A picture for the event, from its feed or its page's link-preview image. https only.
alter table events add column image_url text;
