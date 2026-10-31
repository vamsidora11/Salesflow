-- Runs once, on first initialisation of the database volume.
-- Schema objects (tables, hypertables, aggregates) are created by Alembic from Phase 1.
CREATE EXTENSION IF NOT EXISTS timescaledb;
