import { useEffect, useMemo, useState } from "react";
import type {
  AdvancedModel,
  AdvancedMutation,
  AdvancedMutationPreview,
  AdvancedRecord,
  AdvancedRecordList,
} from "@pkuba/api-client";

import "./advanced-data.css";

type AdminClient = ReturnType<typeof import("@pkuba/api-client").createAdminClient>;

const editableFields: Record<string, string[]> = {
  "competition-groups": ["division", "code", "name", "sort_order"],
  "participant-slots": ["division", "group", "code", "label", "seed"],
};

type AdvancedField = AdvancedModel["fields"][number];

const readableFieldPattern = /(^|_)(name|label|title|status|state|kind|type|code|date|time|score|year|round|stage|gender|venue|team|player|username|filename)(_|$)/i;
const opaqueFieldPattern = /(^id$|_id$|uuid|publication|revision|snapshot|hash|digest|token|password|secret|openid|payload|document|metadata|raw|file_key)/i;
const bookkeepingFieldPattern = /(^|_)(version|created_at|updated_at|deleted_at|purged_at|created_by|updated_by|deleted_by|purged_by)(_|$)/i;

function fieldPriority(field: AdvancedField) {
  if (field.sensitive || /(hash|digest|token|password|secret|openid)/i.test(field.name)) return 90;
  if (field.name === "id") return 85;
  if (opaqueFieldPattern.test(field.name)) return 80;
  if (field.relation) return 70;
  if (bookkeepingFieldPattern.test(field.name)) return 60;
  if (readableFieldPattern.test(field.name)) return 0;
  if (/JSONField|BinaryField/i.test(field.type)) return 50;
  return 20;
}

function orderedModelFields(fields: AdvancedField[]) {
  return fields
    .map((field, index) => ({ field, index }))
    .sort((left, right) => fieldPriority(left.field) - fieldPriority(right.field) || left.index - right.index)
    .map(({ field }) => field);
}

function displayValue(value: unknown) {
  if (value === null) return "null";
  if (value === true) return "true";
  if (value === false) return "false";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function editableValues(modelKey: string, record: AdvancedRecord | null) {
  const allowed = editableFields[modelKey] ?? [];
  return Object.fromEntries(
    allowed.map((field) => [field, record?.values[field] ?? null]),
  );
}

export function AdvancedDataPage({ client }: { client: AdminClient }) {
  const [models, setModels] = useState<AdvancedModel[]>([]);
  const [modelKey, setModelKey] = useState("");
  const [records, setRecords] = useState<AdvancedRecordList | null>(null);
  const [selected, setSelected] = useState<AdvancedRecord | null>(null);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [mutation, setMutation] = useState<AdvancedMutation | null>(null);
  const [mutationText, setMutationText] = useState("{}");
  const [preview, setPreview] = useState<AdvancedMutationPreview | null>(null);

  const model = models.find((item) => item.key === modelKey) ?? null;
  const orderedFields = useMemo(
    () => orderedModelFields(model?.fields ?? []),
    [model],
  );
  const summaryFields = orderedFields.slice(0, 7);

  useEffect(() => {
    client
      .listAdvancedModels()
      .then((items) => {
        setModels(items);
        setModelKey((current) => current || items[0]?.key || "");
      })
      .catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : "无法读取高级数据目录");
      })
      .finally(() => setLoading(false));
  }, []);

  const loadRecords = async (key = modelKey) => {
    if (!key) return;
    setLoading(true);
    setError(null);
    try {
      const next = await client.listAdvancedRecords(key);
      setRecords(next);
      setSelected(null);
      setMutation(null);
      setPreview(null);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "无法读取高级数据记录");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadRecords();
  }, [modelKey]);

  const visibleRecords = useMemo(() => {
    if (!records) return [];
    const needle = query.trim().toLocaleLowerCase();
    if (!needle) return records.items;
    return records.items.filter((item) =>
      JSON.stringify(item.values).toLocaleLowerCase().includes(needle),
    );
  }, [query, records]);

  const openMutation = (operation: "CREATE" | "UPDATE" | "DELETE") => {
    const next: AdvancedMutation = {
      operation,
      object_id: operation === "CREATE" ? null : selected?.id ?? null,
      expected_version:
        operation === "CREATE" || typeof selected?.values.version !== "number"
          ? null
          : selected.values.version,
      values: operation === "DELETE" ? {} : editableValues(modelKey, selected),
    };
    setMutation(next);
    setMutationText(JSON.stringify(next.values ?? {}, null, 2));
    setPreview(null);
    setError(null);
  };

  const previewMutation = async () => {
    if (!mutation || !model) return;
    setBusy(true);
    setError(null);
    try {
      const values = mutation.operation === "DELETE" ? {} : JSON.parse(mutationText);
      const command = { ...mutation, values };
      setMutation(command);
      setPreview(await client.previewAdvancedMutation(model.key, command));
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "高级数据预览失败");
    } finally {
      setBusy(false);
    }
  };

  const applyMutation = async () => {
    if (!mutation || !preview || !model) return;
    if (!window.confirm("确认执行这项高级数据修改？操作将记录完整审计。")) return;
    setBusy(true);
    setError(null);
    try {
      await client.applyAdvancedMutation(model.key, {
        ...mutation,
        impact_hash: preview.impact_hash,
        confirmed: true,
      });
      await loadRecords(model.key);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "高级数据修改失败");
    } finally {
      setBusy(false);
    }
  };

  if (loading && !models.length) {
    return <div className="advanced-state">正在读取高级数据目录…</div>;
  }

  return (
    <div className="advanced-data">
      <aside className="advanced-models">
        <header>
          <span>CORE MODELS</span>
          <strong>{models.length} 个模型</strong>
        </header>
        <nav aria-label="高级数据模型">
          {models.map((item) => (
            <button
              className={item.key === modelKey ? "active" : ""}
              key={item.key}
              type="button"
              onClick={() => setModelKey(item.key)}
            >
              <span>{item.label}</span>
              <small>{item.mutation_mode === "READ_ONLY" ? "只读" : "主数据"}</small>
            </button>
          ))}
        </nav>
      </aside>

      <section className="advanced-records">
        <header className="advanced-heading">
          <div>
            <span>{model?.model_name}</span>
            <h2>{model?.label ?? "高级数据"}</h2>
            <p>完整字段仅用于核心开发者审计；调赛预留场地在生效前仍会脱敏，业务状态必须在对应事务页面修改。</p>
          </div>
          <div>
            <input
              aria-label="筛选当前页记录"
              placeholder="筛选当前 50 条记录"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
            <button className="secondary-action" type="button" onClick={() => void loadRecords()}>
              刷新
            </button>
            {model?.mutation_mode === "VALIDATED_MASTER" && (
              <button className="primary-action" type="button" onClick={() => openMutation("CREATE")}>
                新建
              </button>
            )}
          </div>
        </header>

        {error && <pre className="advanced-error" role="alert">{error}</pre>}
        {loading && <div className="advanced-state">正在读取记录…</div>}
        {!loading && records && (
          <div className="advanced-table-wrap">
            <table className="advanced-table">
              <thead>
                <tr>
                  {summaryFields.map((field) => (
                    <th className={field.sensitive ? "sensitive" : ""} key={field.name}>
                      {field.name}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {visibleRecords.map((record) => (
                  <tr
                    className={selected?.id === record.id ? "selected" : ""}
                    key={record.id}
                    onClick={() => setSelected(record)}
                  >
                    {summaryFields.map((field) => (
                      <td className={field.sensitive ? "sensitive" : ""} key={field.name}>
                        {displayValue(field.name === "id" ? record.id : record.values[field.name])}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {selected && (
        <aside className="advanced-detail" aria-label="完整记录字段">
          <header>
            <div><span>RECORD</span><strong>{selected.id.slice(0, 12)}</strong></div>
            <button type="button" onClick={() => setSelected(null)} aria-label="关闭详情">×</button>
          </header>
          <dl>
            {orderedFields.map((field) => (
              <div className={field.sensitive ? "sensitive" : ""} key={field.name}>
                <dt>{field.name}{field.sensitive && <b>敏感</b>}</dt>
                <dd>{displayValue(field.name === "id" ? selected.id : selected.values[field.name])}</dd>
              </div>
            ))}
          </dl>
          {model?.mutation_mode === "VALIDATED_MASTER" && (
            <footer>
              <button className="secondary-action" type="button" onClick={() => openMutation("UPDATE")}>编辑</button>
              <button className="danger-action" type="button" onClick={() => openMutation("DELETE")}>删除</button>
            </footer>
          )}
        </aside>
      )}

      {mutation && model && (
        <div className="dialog-backdrop" role="presentation" onMouseDown={() => setMutation(null)}>
          <section className="advanced-mutation-dialog" role="dialog" aria-modal="true" onMouseDown={(event) => event.stopPropagation()}>
            <header>
              <div><span>ADVANCED MUTATION</span><h2>{mutation.operation} · {model.label}</h2></div>
              <button type="button" onClick={() => setMutation(null)} aria-label="关闭">×</button>
            </header>
            <p>仅接受该模型允许编辑的字段。关联字段请填写稳定 UUID；提交前服务端会再次校验赛季状态、引用和并发版本。</p>
            {mutation.operation !== "DELETE" && (
              <textarea
                aria-label="高级数据 JSON"
                spellCheck={false}
                value={mutationText}
                onChange={(event) => {
                  setMutationText(event.target.value);
                  setPreview(null);
                }}
              />
            )}
            {preview && (
              <div className={`advanced-preview ${preview.can_apply ? "ready" : "blocked"}`}>
                <strong>{preview.can_apply ? "可以执行" : "存在阻塞"}</strong>
                <span>引用 {Object.values(preview.references).reduce((sum, value) => sum + value, 0)} 条</span>
                {preview.blockers.map((item) => <p key={item.code}>{item.message}</p>)}
              </div>
            )}
            <footer>
              <button className="secondary-action" type="button" onClick={() => setMutation(null)}>取消</button>
              <button className="secondary-action" disabled={busy} type="button" onClick={() => void previewMutation()}>{busy ? "正在检查…" : "服务端预览"}</button>
              <button className="primary-action" disabled={busy || !preview?.can_apply} type="button" onClick={() => void applyMutation()}>二次确认并执行</button>
            </footer>
          </section>
        </div>
      )}
    </div>
  );
}
