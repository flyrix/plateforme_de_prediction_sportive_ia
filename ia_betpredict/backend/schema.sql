-- schema.sql
-- À exécuter une seule fois dans le SQL Editor de Neon
-- Crée la table principale des prédictions

CREATE TABLE IF NOT EXISTS predictions_history (
    id               SERIAL PRIMARY KEY,
    match_date       DATE          NOT NULL,
    match_name       VARCHAR(255)  NOT NULL,
    league           VARCHAR(100)  NOT NULL,
    home_team        VARCHAR(100)  NOT NULL,
    away_team        VARCHAR(100)  NOT NULL,
    match_time       VARCHAR(10)   NOT NULL DEFAULT '',
    prediction_type  VARCHAR(50)   NOT NULL,
    confidence_rate  NUMERIC(5, 4) NOT NULL,
    status           VARCHAR(20)   NOT NULL DEFAULT 'En attente',
    created_at       TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

-- Index pour accélérer les requêtes par date (utilisé dans tous les GET /coupons)
CREATE INDEX IF NOT EXISTS idx_predictions_match_date
    ON predictions_history (match_date);

-- Index composite pour les filtres par date + ligue
CREATE INDEX IF NOT EXISTS idx_predictions_date_league
    ON predictions_history (match_date, league);

-- Contrainte d'unicité pour éviter les doublons lors des re-runs du job
ALTER TABLE predictions_history
    ADD CONSTRAINT uq_prediction
    UNIQUE (match_date, match_name, prediction_type);
