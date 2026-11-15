-- C2: demo isolation. Only accounts explicitly marked as demo can be used by the
-- password-less picker, and sessions record how they were opened so demo
-- sessions can be refused/revoked the moment open demo is switched off.
ALTER TABLE motion.app_user ADD COLUMN is_demo boolean NOT NULL DEFAULT false;
ALTER TABLE motion.session ADD COLUMN origin text NOT NULL DEFAULT 'password'
    CHECK (origin IN ('password', 'demo'));

-- Backfill: accounts created by the TEST DATA seed carry "(TEST" in their name.
UPDATE motion.app_user SET is_demo = true WHERE full_name LIKE '%(TEST%';

-- Technical admin (TEST) needs explicit all-branch scope to register branch devices (H1 device gates).
UPDATE motion.app_user u SET all_branches = true
 WHERE u.is_demo AND EXISTS (SELECT 1 FROM motion.user_role r WHERE r.user_id = u.id AND r.role_code = 'sysadmin');
