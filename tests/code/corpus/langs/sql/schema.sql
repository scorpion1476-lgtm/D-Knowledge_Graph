CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE orders (
    id INTEGER PRIMARY KEY,
    user_id INTEGER
);

CREATE VIEW active_users AS
SELECT id, name FROM users;

CREATE INDEX users_name_idx ON users (name);
