-- points to an isolated screenshot of the ego gift in ui
-- this screenshot is later used to automatically insert training regions
-- into manual_train.json (if the screenshot isn't manually trained beforehand)
ALTER TABLE ego_gifts ADD COLUMN ui_image_path TEXT;
