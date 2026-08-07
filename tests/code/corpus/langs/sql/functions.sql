CREATE FUNCTION user_count() RETURNS INTEGER AS $$
    SELECT count(*) FROM users;
$$ LANGUAGE SQL;

CREATE VIEW order_totals AS
SELECT user_id, count(*) FROM orders GROUP BY user_id;
