# Demo databases

`demo/sales.sql`, `demo/hr.sql` and `demo/inventory.sql` create three small PostgreSQL databases
with generated, fictional data: an online shop, a company's people and pay, and its warehouses.
Each has foreign keys, comments and one view, so the catalog has something real to learn.

The quick start loads them automatically (`docker/quickstart/initdb/00-demo.sh`). To load them
into your own Postgres instead:

```bash
for db in sales hr inventory; do
  createdb "$db" && psql -d "$db" -f "examples/demo/$db.sql"
done
```

Questions that work well on this data:

- Which product category had the most revenue?
- Who are the top 5 customers by total order value?
- How many active employees are in each department?
- How many leave requests are there by type?
- Which items are below their reorder level?
- Which supplier has the most purchase orders still in transit?
