-- Demo "hr" database: people, departments and pay. All data is generated and fictional.
SELECT setseed(0.17);

CREATE TABLE departments (
    department_id serial PRIMARY KEY,
    name          text NOT NULL UNIQUE,
    cost_center   text NOT NULL
);
COMMENT ON TABLE departments IS 'Organisational units.';

CREATE TABLE employees (
    employee_id   serial PRIMARY KEY,
    full_name     text NOT NULL,
    work_email    text NOT NULL UNIQUE,
    department_id int NOT NULL REFERENCES departments(department_id),
    manager_id    int REFERENCES employees(employee_id),
    job_title     text NOT NULL,
    hire_date     date NOT NULL,
    termination_date date,
    national_id   text NOT NULL
);
COMMENT ON TABLE employees IS 'Everyone employed now or in the past.';
COMMENT ON COLUMN employees.national_id IS 'Government ID number (sensitive personal data).';

CREATE TABLE salaries (
    employee_id   int NOT NULL REFERENCES employees(employee_id),
    effective_from date NOT NULL,
    annual_salary numeric(12,2) NOT NULL,
    currency      char(3) NOT NULL DEFAULT 'USD',
    PRIMARY KEY (employee_id, effective_from)
);
COMMENT ON TABLE salaries IS 'Salary history; the latest effective_from is the current salary (confidential).';

CREATE TABLE leave_requests (
    request_id    serial PRIMARY KEY,
    employee_id   int NOT NULL REFERENCES employees(employee_id),
    leave_type    text NOT NULL CHECK (leave_type IN ('annual', 'sick', 'parental', 'unpaid')),
    start_date    date NOT NULL,
    days          int NOT NULL CHECK (days > 0),
    status        text NOT NULL CHECK (status IN ('requested', 'approved', 'rejected'))
);
COMMENT ON TABLE leave_requests IS 'Time-off requests and their approval state.';

INSERT INTO departments (name, cost_center) VALUES
  ('Engineering', 'CC-100'), ('Sales', 'CC-200'), ('Marketing', 'CC-300'),
  ('Finance', 'CC-400'), ('Human Resources', 'CC-500'), ('Operations', 'CC-600'),
  ('Customer Support', 'CC-700');

INSERT INTO employees (full_name, work_email, department_id, manager_id, job_title, hire_date, termination_date, national_id)
SELECT f || ' ' || l,
       lower(f) || '.' || lower(l) || g || '@example.com',
       1 + (g % 7),
       CASE WHEN g > 7 THEN 1 + (g % 7) END,
       CASE WHEN g <= 7 THEN 'Head of Department'
            ELSE (ARRAY['Associate','Specialist','Senior Specialist','Analyst','Engineer','Senior Engineer','Team Lead'])[1 + floor(random()*7)::int] END,
       date '2015-01-01' + (random() * 3500)::int,
       CASE WHEN random() < 0.12 THEN date '2024-01-01' + (random() * 600)::int END,
       'ID-' || lpad((100000 + g * 37)::text, 7, '0')
FROM generate_series(1, 250) g,
     LATERAL (SELECT (ARRAY['Alex','Sam','Jordan','Taylor','Morgan','Casey','Riley','Jamie','Avery','Quinn','Noor','Omar','Lina','Yusuf','Sara','Mina'])[1 + (g * 3) % 16] AS f,
                     (ARRAY['Smith','Garcia','Khan','Chen','Novak','Silva','Okafor','Haddad','Larsen','Rossi','Kim','Patel','Ibrahim'])[1 + (g * 7 + g / 16) % 13] AS l) n;

INSERT INTO salaries (employee_id, effective_from, annual_salary)
SELECT e.employee_id, e.hire_date + (y * 365), round((45000 + random() * 90000 + y * 3000)::numeric, -2)
FROM employees e CROSS JOIN generate_series(0, 3) y
WHERE e.hire_date + (y * 365) < date '2026-01-01';

INSERT INTO leave_requests (employee_id, leave_type, start_date, days, status)
SELECT 1 + floor(random() * 250)::int,
       (ARRAY['annual','annual','annual','sick','sick','parental','unpaid'])[1 + floor(random()*7)::int],
       date '2024-01-01' + (random() * 690)::int,
       1 + floor(random() * 10)::int,
       (ARRAY['approved','approved','approved','requested','rejected'])[1 + floor(random()*5)::int]
FROM generate_series(1, 900);

CREATE VIEW headcount_by_department AS
SELECT d.name AS department, count(*) FILTER (WHERE e.termination_date IS NULL) AS active_employees
FROM departments d LEFT JOIN employees e USING (department_id)
GROUP BY d.name;
COMMENT ON VIEW headcount_by_department IS 'Current (not terminated) employees per department.';
