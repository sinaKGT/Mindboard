-- Which text each card's vector was made from, so an edited card is embedded
-- again and an unchanged one never is (retrieve.py, DESIGN.md 29).
ALTER TABLE node_embedding ADD COLUMN text_hash TEXT NOT NULL DEFAULT '';
