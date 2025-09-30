// frontend_votes_result/server.js
import express from 'express';
import { createPool } from 'mysql2/promise';

const app = express();
const PORT = parseInt(process.env.PORT || '8080', 10);

// DB env (must match your K8s ConfigMap/Secret)
const DB_HOST = process.env.DB_HOST || 'db';
const DB_NAME = process.env.DB_NAME || 'votesdb';
const DB_USER = process.env.DB_USER || 'appuser';
const DB_PASS = process.env.DB_PASS || 'apppassword';

// Create a shared connection pool (global for this process)
const pool = createPool({
  host: DB_HOST,
  user: DB_USER,
  password: DB_PASS,
  database: DB_NAME,
  waitForConnections: true,
  connectionLimit: 10,
  queueLimit: 0,
});

// Simple home page
app.get('/', (req, res) => {
  res.type('html').send(`
    <h1>Vote Results</h1>
    <p>See JSON at <a href="/api/results">/api/results</a></p>
    <p>Health: <a href="/api/health">/api/health</a></p>
  `);
});

// Health check that pings the DB
app.get('/api/health', async (req, res) => {
  try {
    const [rows] = await pool.query('SELECT 1 AS ok');
    res.json({ status: 'ok', db: rows[0].ok === 1 ? 'up' : 'unknown' });
  } catch (err) {
    res.status(500).json({ status: 'error', error: String(err) });
  }
});

// Results: aggregate and map a/b -> breed names
app.get('/api/results', async (req, res) => {
  try {
    const [rows] = await pool.query(
      'SELECT option_value AS code, COUNT(*) AS total FROM votes GROUP BY option_value'
    );

    const label = { a: 'Belgian Malinois', b: 'German Shepherd' };

    // ensure missing options show as 0
    const counts = { a: 0, b: 0 };
    for (const r of rows) counts[r.code] = Number(r.total) || 0;

    const data = [
      { option: label.a, count: counts.a },
      { option: label.b, count: counts.b },
    ];

    res.json(data);
  } catch (err) {
    console.error('results error:', err);
    res.status(500).json({ error: String(err) });
  }
});

app.listen(PORT, () => {
  console.log(`results-api on ${PORT}`);
});
