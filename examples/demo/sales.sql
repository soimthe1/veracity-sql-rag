-- Demo "sales" database: a small online shop. All data is generated and fictional.
SELECT setseed(0.42);

CREATE TABLE regions (
    region_id   serial PRIMARY KEY,
    name        text NOT NULL UNIQUE,
    country     text NOT NULL
);
COMMENT ON TABLE regions IS 'Sales regions the shop ships to.';

CREATE TABLE customers (
    customer_id  serial PRIMARY KEY,
    full_name    text NOT NULL,
    email        text NOT NULL UNIQUE,
    region_id    int NOT NULL REFERENCES regions(region_id),
    segment      text NOT NULL CHECK (segment IN ('consumer', 'small_business', 'enterprise')),
    created_at   date NOT NULL
);
COMMENT ON TABLE customers IS 'People and companies that have placed at least one order.';
COMMENT ON COLUMN customers.email IS 'Contact e-mail (personal data).';

CREATE TABLE products (
    product_id   serial PRIMARY KEY,
    sku          text NOT NULL UNIQUE,
    name         text NOT NULL,
    category     text NOT NULL,
    unit_price   numeric(10,2) NOT NULL CHECK (unit_price > 0),
    is_active    boolean NOT NULL DEFAULT true
);
COMMENT ON TABLE products IS 'Catalogue of products for sale.';

CREATE TABLE orders (
    order_id     serial PRIMARY KEY,
    customer_id  int NOT NULL REFERENCES customers(customer_id),
    order_date   date NOT NULL,
    status       text NOT NULL CHECK (status IN ('pending', 'shipped', 'delivered', 'cancelled', 'returned')),
    channel      text NOT NULL CHECK (channel IN ('web', 'mobile', 'phone'))
);
COMMENT ON TABLE orders IS 'Customer orders, one row per checkout.';

CREATE TABLE order_items (
    order_id     int NOT NULL REFERENCES orders(order_id),
    line_no      int NOT NULL,
    product_id   int NOT NULL REFERENCES products(product_id),
    quantity     int NOT NULL CHECK (quantity > 0),
    unit_price   numeric(10,2) NOT NULL,
    discount_pct numeric(5,2) NOT NULL DEFAULT 0,
    PRIMARY KEY (order_id, line_no)
);
COMMENT ON TABLE order_items IS 'Products on each order, with the price paid.';

INSERT INTO regions (name, country) VALUES
  ('North', 'Northland'), ('South', 'Southland'), ('East', 'Eastmark'),
  ('West', 'Westvale'), ('Central', 'Midland');

INSERT INTO customers (full_name, email, region_id, segment, created_at)
SELECT f || ' ' || l,
       lower(f) || '.' || lower(l) || g || '@example.com',
       1 + (g % 5),
       (ARRAY['consumer','consumer','consumer','small_business','enterprise'])[1 + (g % 5)],
       date '2023-01-01' + (random() * 900)::int
FROM generate_series(1, 400) g,
     LATERAL (SELECT (ARRAY['Alex','Sam','Jordan','Taylor','Morgan','Casey','Riley','Jamie','Avery','Quinn','Noor','Omar','Lina','Yusuf','Sara'])[1 + g % 15] AS f,
                     (ARRAY['Smith','Garcia','Khan','Chen','Novak','Silva','Okafor','Haddad','Larsen','Rossi','Kim','Patel'])[1 + (g / 15) % 12] AS l) n;

INSERT INTO products (sku, name, category, unit_price, is_active)
SELECT 'SKU-' || lpad(g::text, 4, '0'),
       (ARRAY['Desk Lamp','Office Chair','Standing Desk','Monitor 27"','Keyboard','Mouse','Headset','Webcam','Notebook Pack','Coffee Beans 1kg','Water Bottle','Backpack'])[1 + (g % 12)] || ' ' || chr(65 + (g % 5)),
       (ARRAY['Furniture','Furniture','Furniture','Electronics','Electronics','Electronics','Electronics','Electronics','Stationery','Grocery','Accessories','Accessories'])[1 + (g % 12)],
       round((10 + random() * 490)::numeric, 2),
       g % 17 <> 0
FROM generate_series(1, 60) g;

INSERT INTO orders (customer_id, order_date, status, channel)
SELECT 1 + floor(random() * 400)::int,
       date '2024-01-01' + (random() * 690)::int,
       (ARRAY['delivered','delivered','delivered','shipped','pending','cancelled','returned'])[1 + floor(random()*7)::int],
       (ARRAY['web','web','mobile','phone'])[1 + floor(random()*4)::int]
FROM generate_series(1, 3000);

INSERT INTO order_items (order_id, line_no, product_id, quantity, unit_price, discount_pct)
SELECT o.order_id, l.line_no, p.product_id, 1 + floor(random() * 4)::int, p.unit_price,
       (ARRAY[0,0,0,5,10,15])[1 + floor(random()*6)::int]
FROM orders o
CROSS JOIN LATERAL generate_series(1, 1 + (o.order_id % 4)) AS l(line_no)
CROSS JOIN LATERAL (SELECT product_id, unit_price FROM products
                    WHERE product_id = 1 + ((o.order_id * 7 + l.line_no * 13) % 60)) p;

CREATE VIEW monthly_revenue AS
SELECT date_trunc('month', o.order_date)::date AS month,
       sum(i.quantity * i.unit_price * (1 - i.discount_pct / 100)) AS revenue,
       count(DISTINCT o.order_id) AS orders
FROM orders o JOIN order_items i USING (order_id)
WHERE o.status IN ('shipped', 'delivered')
GROUP BY 1;
COMMENT ON VIEW monthly_revenue IS 'Revenue from shipped and delivered orders, per month.';
