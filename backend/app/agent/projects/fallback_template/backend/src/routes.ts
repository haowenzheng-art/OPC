import { Router } from 'express';
import { z } from 'zod';

import { prisma } from './db.js';

const router = Router();

const createSchema = z.object({
  title: z.string().trim().min(1).max(500),
});

const updateSchema = z
  .object({
    title: z.string().trim().min(1).max(500).optional(),
    completed: z.boolean().optional(),
  })
  .refine((data) => data.title !== undefined || data.completed !== undefined, {
    message: 'At least one of title or completed must be provided',
  });

const filterSchema = z.object({
  filter: z.enum(['all', 'active', 'completed']).optional(),
});

type TodoPayload = {
  id: string;
  title: string;
  completed: boolean;
  createdAt: string;
  updatedAt: string;
};

function serialize(todo: { id: string; title: string; completed: boolean; createdAt: Date; updatedAt: Date }): TodoPayload {
  return {
    id: todo.id,
    title: todo.title,
    completed: todo.completed,
    createdAt: todo.createdAt.toISOString(),
    updatedAt: todo.updatedAt.toISOString(),
  };
}

// GET /api/v1/todos — list all todos (optional ?filter=active|completed)
router.get('/todos', async (req, res, next) => {
  try {
    const { filter } = filterSchema.parse(req.query);
    const where = filter === 'active' ? { completed: false } : filter === 'completed' ? { completed: true } : undefined;
    const rows = await prisma.todo.findMany({ where, orderBy: { createdAt: 'desc' } });
    res.json({ data: rows.map(serialize) });
  } catch (err) {
    next(err);
  }
});

// POST /api/v1/todos — create a new todo
router.post('/todos', async (req, res, next) => {
  try {
    const { title } = createSchema.parse(req.body);
    const created = await prisma.todo.create({ data: { title, completed: false } });
    res.status(201).json({ data: serialize(created) });
  } catch (err) {
    next(err);
  }
});

// PUT /api/v1/todos/:id — partial update (title or completed)
router.put('/todos/:id', async (req, res, next) => {
  try {
    const { id } = req.params;
    const patch = updateSchema.parse(req.body);
    const existing = await prisma.todo.findUnique({ where: { id } });
    if (!existing) {
      return res.status(404).json({ error: { code: 'not_found', message: `Todo ${id} not found` } });
    }
    const updated = await prisma.todo.update({
      where: { id },
      data: {
        title: patch.title ?? undefined,
        completed: patch.completed ?? undefined,
      },
    });
    res.json({ data: serialize(updated) });
  } catch (err) {
    next(err);
  }
});

// DELETE /api/v1/todos/:id
router.delete('/todos/:id', async (req, res, next) => {
  try {
    const { id } = req.params;
    const existing = await prisma.todo.findUnique({ where: { id } });
    if (!existing) {
      return res.status(404).json({ error: { code: 'not_found', message: `Todo ${id} not found` } });
    }
    await prisma.todo.delete({ where: { id } });
    res.json({ data: { id } });
  } catch (err) {
    next(err);
  }
});

export default router;
