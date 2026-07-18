-- ui_image_path points at a locally-provided alternate art asset for a
-- gift (resources/Gift/<Affinity>/<Name>.webp), matched by name against
-- GLL's scraped gifts. NULL until matched.

ALTER TABLE ego_gifts ADD COLUMN ui_image_path TEXT;
