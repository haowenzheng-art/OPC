'use client';

import { FormEvent, useEffect, useMemo, useState } from 'react';

// API base URL — env override first, then default to local backend.
const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:3001/api/v1';
const LS_KEY = 'fallback-todo-app:v1';

type Todo = {
  id: string;
  title: string;
  completed: boolean;
  createdAt?: string;
  updatedAt?: string;
};

type Filter = 'all' | 'active' | 'completed';

function readLocal(): Todo[] {
  if (typeof window === 'undefined') return [];
  try {
    const raw = window.localStorage.getItem(LS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(isTodo).map((t) => ({ ...t, completed: !!t.completed }));
  } catch {
    return [];
  }
}

function writeLocal(todos: Todo[]): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(LS_KEY, JSON.stringify(todos));
  } catch {
    // quota / private mode — silently degrade
  }
}

function isTodo(value: unknown): value is Todo {
  if (typeof value !== 'object' || value === null) return false;
  const v = value as Record<string, unknown>;
  return typeof v.id === 'string' && typeof v.title === 'string';
}

function newLocalId(): string {
  return `local-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

export default function Home() {
  const [todos, setTodos] = useState<Todo[]>([]);
  const [filter, setFilter] = useState<Filter>('all');
  const [draft, setDraft] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [backendDown, setBackendDown] = useState(false);

  // Load on mount: try backend, fall back to localStorage.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const res = await fetch(`${API}/todos`, { cache: 'no-store' });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const json = (await res.json()) as { data?: Todo[] };
        if (cancelled) return;
        const list = Array.isArray(json.data) ? json.data : [];
        setTodos(list);
        writeLocal(list);
        setBackendDown(false);
        setError(null);
      } catch (err) {
        if (cancelled) return;
        const local = readLocal();
        setTodos(local);
        setBackendDown(true);
        setError('Offline mode — using local storage');
        console.warn('backend unreachable, falling back to localStorage:', err);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const filtered = useMemo(() => {
    if (filter === 'active') return todos.filter((t) => !t.completed);
    if (filter === 'completed') return todos.filter((t) => t.completed);
    return todos;
  }, [todos, filter]);

  const remaining = useMemo(() => todos.filter((t) => !t.completed).length, [todos]);

  // Persist every change to localStorage so backend-down edits survive reload.
  useEffect(() => {
    if (todos.length > 0 || readLocal().length > 0) {
      writeLocal(todos);
    }
  }, [todos]);

  async function addTodo(e: FormEvent) {
    e.preventDefault();
    const title = draft.trim();
    if (!title) return;
    const optimistic: Todo = {
      id: newLocalId(),
      title,
      completed: false,
      createdAt: new Date().toISOString(),
    };
    setTodos((prev) => [optimistic, ...prev]);
    setDraft('');
    setError(null);

    if (backendDown) return;

    try {
      const res = await fetch(`${API}/todos`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = (await res.json()) as { data?: Todo };
      if (json.data) {
        setTodos((prev) => prev.map((t) => (t.id === optimistic.id ? { ...json.data!, title } : t)));
      }
    } catch {
      setBackendDown(true);
      setError('Backend unreachable — new todo saved locally');
    }
  }

  async function toggleTodo(id: string) {
    const target = todos.find((t) => t.id === id);
    if (!target) return;
    const nextCompleted = !target.completed;
    setTodos((prev) => prev.map((t) => (t.id === id ? { ...t, completed: nextCompleted } : t)));
    setError(null);

    if (backendDown || id.startsWith('local-')) return;

    try {
      const res = await fetch(`${API}/todos/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ completed: nextCompleted }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
    } catch {
      setBackendDown(true);
      setError('Backend unreachable — change saved locally');
    }
  }

  async function deleteTodo(id: string) {
    const prev = todos;
    setTodos((p) => p.filter((t) => t.id !== id));
    setError(null);

    if (backendDown || id.startsWith('local-')) return;

    try {
      const res = await fetch(`${API}/todos/${id}`, { method: 'DELETE' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
    } catch {
      setBackendDown(true);
      setError('Backend unreachable — delete saved locally');
      // Keep local delete in place even if backend rejects.
      void prev;
    }
  }

  function clearCompleted() {
    const completedIds = todos.filter((t) => t.completed).map((t) => t.id);
    setTodos((prev) => prev.filter((t) => !t.completed));
    if (backendDown) return;
    for (const id of completedIds) {
      if (id.startsWith('local-')) continue;
      fetch(`${API}/todos/${id}`, { method: 'DELETE' }).catch(() => {
        setBackendDown(true);
      });
    }
  }

  return (
    <main className="min-h-screen bg-gray-50 py-10 px-4">
      <div className="max-w-2xl mx-auto bg-white rounded-2xl shadow-sm border border-gray-200 p-6 sm:p-8 space-y-6">
        <header className="space-y-1">
          <h1 className="text-3xl font-bold text-gray-900 tracking-tight">Todos</h1>
          <p className="text-sm text-gray-500">Stay focused. One thing at a time.</p>
        </header>

        {error && (
          <div
            role="status"
            data-testid="backend-banner"
            className="p-3 rounded-lg bg-amber-50 text-amber-800 text-sm border border-amber-200"
          >
            {error}
          </div>
        )}

        <form onSubmit={addTodo} className="flex gap-2">
          <input
            type="text"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="What needs to be done?"
            aria-label="New todo title"
            data-testid="todo-input"
            className="flex-1 px-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
          />
          <button
            type="submit"
            data-testid="add-btn"
            disabled={!draft.trim()}
            className="px-6 py-2 bg-blue-500 text-white rounded-lg hover:bg-blue-600 transition disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Add
          </button>
        </form>

        <div className="flex items-center gap-2 border-b border-gray-200 pb-2" role="tablist" aria-label="Filter todos">
          {(['all', 'active', 'completed'] as const).map((f) => (
            <button
              key={f}
              role="tab"
              aria-selected={filter === f}
              data-testid={`filter-${f}`}
              onClick={() => setFilter(f)}
              className={`px-3 py-1.5 text-sm rounded-md transition ${
                filter === f
                  ? 'bg-blue-500 text-white'
                  : 'text-gray-600 hover:bg-gray-100'
              }`}
            >
              {f.charAt(0).toUpperCase() + f.slice(1)}
            </button>
          ))}
          <span className="ml-auto text-xs text-gray-500" data-testid="count">
            {remaining} {remaining === 1 ? 'item' : 'items'} left
          </span>
        </div>

        {loading ? (
          <p className="text-gray-500 text-center py-8" data-testid="loading">
            Loading…
          </p>
        ) : filtered.length === 0 ? (
          <p className="text-gray-500 text-center py-8" data-testid="empty">
            {todos.length === 0 ? 'No todos yet. Add one above.' : `No ${filter} todos.`}
          </p>
        ) : (
          <ul className="space-y-2" data-testid="todo-list">
            {filtered.map((todo) => (
              <li
                key={todo.id}
                data-testid={`todo-item-${todo.id}`}
                className="flex items-center gap-3 p-3 bg-white rounded-lg shadow-sm border border-gray-100"
              >
                <input
                  type="checkbox"
                  checked={todo.completed}
                  onChange={() => toggleTodo(todo.id)}
                  aria-label={`Mark ${todo.title} as ${todo.completed ? 'active' : 'completed'}`}
                  data-testid={`toggle-${todo.id}`}
                  className="w-5 h-5 accent-blue-500 cursor-pointer"
                />
                <span
                  className={`flex-1 text-gray-900 ${
                    todo.completed ? 'line-through text-gray-400' : ''
                  }`}
                >
                  {todo.title}
                </span>
                <button
                  onClick={() => deleteTodo(todo.id)}
                  aria-label={`Delete ${todo.title}`}
                  data-testid={`delete-${todo.id}`}
                  className="text-red-500 hover:text-red-700 text-sm font-medium"
                >
                  Delete
                </button>
              </li>
            ))}
          </ul>
        )}

        {todos.some((t) => t.completed) && (
          <div className="pt-2 border-t border-gray-100 flex justify-end">
            <button
              onClick={clearCompleted}
              data-testid="clear-completed"
              className="text-xs text-gray-500 hover:text-gray-700 underline"
            >
              Clear completed
            </button>
          </div>
        )}
      </div>
    </main>
  );
}
