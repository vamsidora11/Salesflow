import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { ApiError } from "../api/client";
import { createProduct, createStore, listProducts, listStores } from "../api/endpoints";
import type { StoreCreated } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import WidgetState from "../components/WidgetState";
import { errorMessage, formatMoney } from "../lib/format";

const PRICE_RE = /^\d+\.\d{2}$/;

function ApiKeyBox({ store, onDismiss }: { store: StoreCreated; onDismiss: () => void }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(store.api_key);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }
  return (
    <div className="apikey-box" role="alert">
      <p>
        <strong>API key for {store.name}</strong>
      </p>
      <p className="warning">Copy it now. This key will not be shown again.</p>
      <input readOnly aria-label="Store API key" value={store.api_key} onFocus={(e) => e.currentTarget.select()} />
      <div className="actions">
        <button type="button" onClick={() => void copy()}>
          {copied ? "Copied" : "Copy key"}
        </button>
        <button type="button" className="secondary" onClick={onDismiss}>
          I have saved it
        </button>
      </div>
    </div>
  );
}

function StoresSection() {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [region, setRegion] = useState("");
  const [timezone, setTimezone] = useState("UTC");
  const [created, setCreated] = useState<StoreCreated | null>(null);

  const list = useInfiniteQuery({
    queryKey: ["admin", "stores"],
    queryFn: ({ pageParam }) => listStores({ limit: 50, cursor: pageParam }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
  const rows = list.data?.pages.flatMap((p) => p.items) ?? [];

  const create = useMutation({
    mutationFn: () => createStore({ name: name.trim(), region: region.trim(), timezone: timezone.trim() || "UTC" }),
    onSuccess: (store) => {
      setCreated(store);
      setName("");
      setRegion("");
      void qc.invalidateQueries({ queryKey: ["admin", "stores"] });
      void qc.invalidateQueries({ queryKey: ["stores"] });
    },
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    create.mutate();
  }

  return (
    <section className="widget" aria-labelledby="stores-heading">
      <h2 id="stores-heading">Stores</h2>
      <form className="inline-form" onSubmit={submit} aria-label="Create store">
        <div className="field">
          <label htmlFor="s-name">Name</label>
          <input id="s-name" required maxLength={200} value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="s-region">Region</label>
          <input id="s-region" required maxLength={100} value={region} onChange={(e) => setRegion(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="s-tz">Timezone</label>
          <input id="s-tz" value={timezone} onChange={(e) => setTimezone(e.target.value)} placeholder="e.g. Europe/Berlin" />
        </div>
        <button type="submit" disabled={create.isPending}>
          {create.isPending ? "Creating…" : "Create store"}
        </button>
      </form>
      <div aria-live="polite">
        {create.isError && (
          <p className="field-error" role="alert">
            Could not create store: {errorMessage(create.error)}
          </p>
        )}
      </div>
      {created && <ApiKeyBox store={created} onDismiss={() => setCreated(null)} />}

      <WidgetState
        label="stores"
        isLoading={list.isLoading}
        error={list.error}
        isEmpty={rows.length === 0}
        emptyMessage="No stores yet. Create the first one above."
        onRetry={() => void list.refetch()}
      >
        <table className="data-table">
          <caption className="visually-hidden">Stores</caption>
          <thead>
            <tr>
              <th scope="col">Name</th>
              <th scope="col">Region</th>
              <th scope="col">Timezone</th>
              <th scope="col">Active</th>
              <th scope="col">Created</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((s) => (
              <tr key={s.id}>
                <td>{s.name}</td>
                <td>{s.region}</td>
                <td>{s.timezone}</td>
                <td>{s.is_active ? "Yes" : "No"}</td>
                <td>{new Date(s.created_at).toLocaleDateString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {list.hasNextPage && (
          <button type="button" className="secondary" onClick={() => void list.fetchNextPage()} disabled={list.isFetchingNextPage}>
            {list.isFetchingNextPage ? "Loading…" : "Load more"}
          </button>
        )}
      </WidgetState>
    </section>
  );
}

function ProductsSection() {
  const qc = useQueryClient();
  const [sku, setSku] = useState("");
  const [name, setName] = useState("");
  const [category, setCategory] = useState("");
  const [price, setPrice] = useState("");
  const [priceError, setPriceError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);

  const list = useInfiniteQuery({
    queryKey: ["admin", "products"],
    queryFn: ({ pageParam }) => listProducts({ limit: 50, cursor: pageParam }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
  const rows = list.data?.pages.flatMap((p) => p.items) ?? [];

  const create = useMutation({
    mutationFn: () =>
      createProduct({ sku: sku.trim(), name: name.trim(), category: category.trim(), list_price: price.trim() }),
    onSuccess: (p) => {
      setOk(`Created product ${p.sku}.`);
      setSku("");
      setName("");
      setCategory("");
      setPrice("");
      void qc.invalidateQueries({ queryKey: ["admin", "products"] });
    },
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    setOk(null);
    if (!PRICE_RE.test(price.trim())) {
      setPriceError("Enter the price with exactly 2 decimals, e.g. 19.99.");
      return;
    }
    setPriceError(null);
    create.mutate();
  }

  const err = create.error;
  const errText =
    err instanceof ApiError && err.status === 409 ? `A product with SKU "${sku}" already exists.` : err ? errorMessage(err) : null;

  return (
    <section className="widget" aria-labelledby="products-heading">
      <h2 id="products-heading">Products</h2>
      <form className="inline-form" onSubmit={submit} aria-label="Create product">
        <div className="field">
          <label htmlFor="p-sku">SKU</label>
          <input id="p-sku" required maxLength={64} value={sku} onChange={(e) => setSku(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="p-name">Name</label>
          <input id="p-name" required maxLength={200} value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="p-cat">Category</label>
          <input id="p-cat" required maxLength={100} value={category} onChange={(e) => setCategory(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="p-price">List price</label>
          <input
            id="p-price"
            required
            inputMode="decimal"
            placeholder="19.99"
            value={price}
            aria-invalid={!!priceError}
            aria-describedby={priceError ? "p-price-err" : undefined}
            onChange={(e) => setPrice(e.target.value)}
          />
        </div>
        <button type="submit" disabled={create.isPending}>
          {create.isPending ? "Creating…" : "Create product"}
        </button>
      </form>
      <div aria-live="polite">
        {priceError && (
          <p id="p-price-err" className="field-error" role="alert">
            {priceError}
          </p>
        )}
        {errText && (
          <p className="field-error" role="alert">
            {errText}
          </p>
        )}
        {ok && <p>{ok}</p>}
      </div>

      <WidgetState
        label="products"
        isLoading={list.isLoading}
        error={list.error}
        isEmpty={rows.length === 0}
        emptyMessage="No products yet. Create the first one above."
        onRetry={() => void list.refetch()}
      >
        <table className="data-table">
          <caption className="visually-hidden">Products</caption>
          <thead>
            <tr>
              <th scope="col">SKU</th>
              <th scope="col">Name</th>
              <th scope="col">Category</th>
              <th scope="col" className="num">
                List price
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((p) => (
              <tr key={p.id}>
                <td>{p.sku}</td>
                <td>{p.name}</td>
                <td>{p.category}</td>
                <td className="num">{formatMoney(p.list_price)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {list.hasNextPage && (
          <button type="button" className="secondary" onClick={() => void list.fetchNextPage()} disabled={list.isFetchingNextPage}>
            {list.isFetchingNextPage ? "Loading…" : "Load more"}
          </button>
        )}
      </WidgetState>
    </section>
  );
}

export default function Admin() {
  const { role } = useAuth();
  if (role !== "admin") {
    return (
      <div>
        <h1>Stores and products</h1>
        <p className="state state-empty">This page is only available to administrators.</p>
      </div>
    );
  }
  return (
    <div>
      <h1>Stores and products</h1>
      <StoresSection />
      <ProductsSection />
    </div>
  );
}
