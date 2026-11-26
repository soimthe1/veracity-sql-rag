-- Demo "inventory" database: warehouses, stock and suppliers. All data is generated and fictional.
SELECT setseed(0.73);

CREATE TABLE warehouses (
    warehouse_id serial PRIMARY KEY,
    code         text NOT NULL UNIQUE,
    city         text NOT NULL,
    capacity_m3  int NOT NULL
);
COMMENT ON TABLE warehouses IS 'Storage locations.';

CREATE TABLE suppliers (
    supplier_id  serial PRIMARY KEY,
    name         text NOT NULL UNIQUE,
    country      text NOT NULL,
    rating       int NOT NULL CHECK (rating BETWEEN 1 AND 5)
);
COMMENT ON TABLE suppliers IS 'Companies we buy stock from.';

CREATE TABLE items (
    item_id      serial PRIMARY KEY,
    sku          text NOT NULL UNIQUE,
    description  text NOT NULL,
    supplier_id  int NOT NULL REFERENCES suppliers(supplier_id),
    reorder_level int NOT NULL
);
COMMENT ON TABLE items IS 'Stock-keeping units held in the warehouses (sku matches sales.products.sku).';

CREATE TABLE stock_levels (
    warehouse_id int NOT NULL REFERENCES warehouses(warehouse_id),
    item_id      int NOT NULL REFERENCES items(item_id),
    on_hand      int NOT NULL CHECK (on_hand >= 0),
    updated_at   timestamp NOT NULL,
    PRIMARY KEY (warehouse_id, item_id)
);
COMMENT ON TABLE stock_levels IS 'Current quantity of each item in each warehouse.';

CREATE TABLE purchase_orders (
    po_id        serial PRIMARY KEY,
    supplier_id  int NOT NULL REFERENCES suppliers(supplier_id),
    item_id      int NOT NULL REFERENCES items(item_id),
    quantity     int NOT NULL CHECK (quantity > 0),
    unit_cost    numeric(10,2) NOT NULL,
    ordered_on   date NOT NULL,
    received_on  date
);
COMMENT ON TABLE purchase_orders IS 'Orders placed with suppliers; received_on is empty while in transit.';

INSERT INTO warehouses (code, city, capacity_m3) VALUES
  ('WH-N1', 'Northport', 12000), ('WH-S1', 'Southbay', 9000),
  ('WH-E1', 'Eastfield', 7000), ('WH-W1', 'Westbrook', 15000);

INSERT INTO suppliers (name, country, rating)
SELECT s, c, 1 + floor(random() * 5)::int
FROM unnest(ARRAY['Acme Components','Globex Supply','Initech Parts','Umbrella Goods','Stark Industrial','Wayne Logistics','Hooli Hardware','Vandelay Imports'],
            ARRAY['Northland','Southland','Eastmark','Westvale','Midland','Northland','Eastmark','Westvale']) AS t(s, c);

INSERT INTO items (sku, description, supplier_id, reorder_level)
SELECT 'SKU-' || lpad(g::text, 4, '0'), 'Stock item ' || g, 1 + (g % 8), 20 + (g % 5) * 10
FROM generate_series(1, 60) g;

INSERT INTO stock_levels (warehouse_id, item_id, on_hand, updated_at)
SELECT w.warehouse_id, i.item_id, floor(random() * 300)::int,
       timestamp '2025-11-01' + random() * interval '25 days'
FROM warehouses w CROSS JOIN items i;

INSERT INTO purchase_orders (supplier_id, item_id, quantity, unit_cost, ordered_on, received_on)
SELECT i.supplier_id, i.item_id, 50 + floor(random() * 200)::int,
       round((5 + random() * 300)::numeric, 2), d,
       CASE WHEN random() < 0.85 THEN d + (3 + floor(random() * 20)::int) END
FROM generate_series(1, 700) g
CROSS JOIN LATERAL (SELECT * FROM items WHERE item_id = 1 + (g % 60)) i
CROSS JOIN LATERAL (SELECT date '2024-01-01' + (random() * 690)::int AS d) dd;

CREATE VIEW items_below_reorder AS
SELECT i.sku, w.code AS warehouse, s.on_hand, i.reorder_level
FROM stock_levels s JOIN items i USING (item_id) JOIN warehouses w USING (warehouse_id)
WHERE s.on_hand < i.reorder_level;
COMMENT ON VIEW items_below_reorder IS 'Stock that has fallen below its reorder level.';
