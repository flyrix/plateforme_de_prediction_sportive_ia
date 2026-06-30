-- schema.sql
-- Correspond exactement à la table Neon existante
-- NE PAS ré-exécuter si la table existe déjà

CREATE EXTENSION IF NOT EXISTS "pgcrypto"; -- nécessaire pour gen_random_uuid()

CREATE TABLE IF NOT EXISTS predictions_history (
    id               UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at       TIMESTAMPTZ   NOT NULL DEFAULT now(),
    match_date       DATE          NOT NULL,
    match_name       TEXT          NOT NULL,
    league           TEXT          NOT NULL,
    home_team        TEXT          NOT NULL,
    away_team        TEXT          NOT NULL,
    match_time       TEXT,
    prediction_type  TEXT          NOT NULL,
    confidence_rate  NUMERIC(5, 4) NOT NULL,
    status           TEXT          NOT NULL DEFAULT 'En attente',
    CONSTRAINT predictions_history_status_check
        CHECK (status = ANY (ARRAY['En attente'::text, 'Gagné'::text, 'Perdu'::text, 'Annulé'::text])),
    CONSTRAINT predictions_history_pkey PRIMARY KEY (id)
);

-- Index pour accélérer les requêtes par date
CREATE INDEX IF NOT EXISTS idx_predictions_match_date
    ON predictions_history (match_date);

-- Index composite pour les filtres par date + ligue
CREATE INDEX IF NOT EXISTS idx_predictions_date_league
    ON predictions_history (match_date, league);

-- Contrainte d'unicité pour éviter les doublons lors des re-runs du job
CREATE UNIQUE INDEX IF NOT EXISTS uq_prediction
    ON predictions_history (match_date, match_name, prediction_type);
