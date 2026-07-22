CREATE TABLE IF NOT EXISTS ego_gifts (
  id              TEXT PRIMARY KEY,
  name            TEXT NOT NULL,
  sin             TEXT,
  affinity        TEXT,
  tier            INTEGER,
  buying_cost     INTEGER,
  selling_cost    INTEGER,
  obtain_method   TEXT,
  description     TEXT,
  uptie2          TEXT,
  uptie3          TEXT,
  ingredients     TEXT,
  original_id     INTEGER,
  sort_id         INTEGER GENERATED ALWAYS AS (
                      CASE WHEN original_id = 1005 THEN 403
                           WHEN original_id = 1034 THEN 412
                           WHEN original_id = 1051 THEN 411
                           WHEN original_id = 2048 THEN 500
                           WHEN original_id = 2050 THEN 501
                           WHEN original_id = 2052 THEN 502
                           WHEN original_id = 2053 THEN 503
                           WHEN original_id = 2058 THEN 8
                           WHEN original_id = 2059 THEN 47
                           WHEN original_id = 2060 THEN 36
                           WHEN original_id = 9242 THEN 99242
                           WHEN original_id BETWEEN 9000 AND 9999 THEN original_id - 9000
                           WHEN original_id BETWEEN 1000 AND 1999 THEN original_id - 500
                           WHEN original_id BETWEEN 2000 AND 2065 THEN original_id - 1315
                           WHEN original_id BETWEEN 2066 AND 2079 THEN original_id - 1265
                           WHEN original_id BETWEEN 2080 AND 2999 THEN original_id - 1298
                           ELSE original_id
                      END
                  ) VIRTUAL,
  image_path      TEXT
);
