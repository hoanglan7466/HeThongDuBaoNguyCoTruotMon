-- MySQL 8 bootstrap. SQLAlchemy creates the normalized tables and constraints.
CREATE DATABASE IF NOT EXISTS he_thong_du_bao CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
-- Create a least-privilege account separately, then set DATABASE_URL in .env.
