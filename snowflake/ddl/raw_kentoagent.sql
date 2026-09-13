-- KentoAgent raw schema DDL for Snowflake
-- Run once to set up the database and schema.

CREATE DATABASE IF NOT EXISTS KENTOAGENT;
USE DATABASE KENTOAGENT;
CREATE SCHEMA IF NOT EXISTS RAW_KENTOAGENT;
USE SCHEMA RAW_KENTOAGENT;

-- Scenario seed registry
CREATE TABLE IF NOT EXISTS scenarios (
    seed                INTEGER       NOT NULL,
    generated_at        TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    generation_version  VARCHAR(10)   NOT NULL DEFAULT 'v1',
    area_id             VARCHAR(100),
    min_latitude        FLOAT,
    min_longitude       FLOAT,
    max_latitude        FLOAT,
    max_longitude       FLOAT,
    configuration       VARIANT,
    PRIMARY KEY (seed, generation_version)
);

-- Point-based road blockages
CREATE TABLE IF NOT EXISTS blockages (
    blockage_id         VARCHAR(50)   NOT NULL,
    seed                INTEGER       NOT NULL,
    latitude            FLOAT         NOT NULL,
    longitude           FLOAT         NOT NULL,
    severity            VARCHAR(20)   NOT NULL,
    active              BOOLEAN       NOT NULL DEFAULT TRUE,
    road_segment_id     VARCHAR(100),
    blockage_type       VARCHAR(30),
    generation_version  VARCHAR(10)   NOT NULL DEFAULT 'v1',
    created_at          TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    metadata            VARIANT,
    PRIMARY KEY (blockage_id, seed, generation_version)
);

-- Survivors (visible injured and trapped)
CREATE TABLE IF NOT EXISTS survivors (
    survivor_id         VARCHAR(50)   NOT NULL,
    seed                INTEGER       NOT NULL,
    latitude            FLOAT         NOT NULL,
    longitude           FLOAT         NOT NULL,
    injury_severity     VARCHAR(20)   NOT NULL,
    status              VARCHAR(20)   NOT NULL DEFAULT 'waiting',
    visible             BOOLEAN       NOT NULL,
    trapped             BOOLEAN       NOT NULL,
    generation_version  VARCHAR(10)   NOT NULL DEFAULT 'v1',
    created_at          TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    PRIMARY KEY (survivor_id, seed, generation_version)
);
