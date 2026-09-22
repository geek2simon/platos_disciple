-- =====================================================================
-- Plato's Disciple / Local Knowledge Base
-- Complete PostgreSQL 17 database creation script
-- =====================================================================
-- Default database name: pd_market_kb
-- The database may use any valid name. If you change it, reconnect to that
-- database before running Part 2 and use the same name in the application
-- connection configuration.
--
-- DBeaver / GUI execution requires two stages because PostgreSQL cannot
-- switch databases inside an existing connection:
--   1. Connect to the postgres maintenance database. Highlight and execute
--      only the CREATE DATABASE statement in Part 1.
--   2. Reconnect DBeaver to pd_market_kb (or the name you selected).
--   3. Execute Part 2 from BEGIN through the validation queries at the end.
--
-- The schema section uses the currently connected database and does not
-- contain a hard-coded database name. It is intended for a new, empty
-- database and does not drop existing tables or data.
-- =====================================================================

-- =====================================================================
-- Part 1: Create the database
-- Run this statement separately while connected to the postgres database.
-- =====================================================================
CREATE DATABASE pd_market_kb WITH ENCODING 'UTF8';
-- STOP HERE, reconnect to pd_market_kb, then continue with Part 2.

