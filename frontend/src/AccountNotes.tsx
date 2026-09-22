import { useEffect, useRef, useState } from "react";
import {
  Alert,
  App,
  Button,
  Drawer,
  Empty,
  Form,
  Input,
  Popover,
  Spin,
} from "antd";
import { FileTextOutlined } from "@ant-design/icons";
import { api, ApiError, getSessionVersion, type AccountNote } from "./api";
import { fmt } from "./ui";

function NoteList({ notes }: { notes: AccountNote[] }) {
  return (
    <div className="account-notes-list">
      {notes.map((note) => (
        <article key={note.id}>
          <p className="account-note-content">{note.content}</p>
          <div className="small muted">
            {note.author_name}
            {note.author_name !== note.author_username &&
              `（${note.author_username}）`}{" "}
            · <time dateTime={note.created_at}>{fmt(note.created_at)}</time>
          </div>
        </article>
      ))}
    </div>
  );
}

export function AccountNotes({
  accountId,
  count,
  epoch,
  refresh,
  canAdd = false,
  entry = false,
}: {
  accountId: string;
  count: number;
  epoch: number;
  refresh: () => void;
  canAdd?: boolean;
  entry?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [hover, setHover] = useState(false);
  const [preview, setPreview] = useState<AccountNote[]>();
  const [previewError, setPreviewError] = useState("");
  useEffect(() => {
    const clear = () => {
      setOpen(false);
      setHover(false);
      setPreview(undefined);
    };
    window.addEventListener("session-changed", clear);
    return () => window.removeEventListener("session-changed", clear);
  }, []);
  useEffect(() => {
    if (!hover || open) return;
    const controller = new AbortController();
    setPreview(undefined);
    setPreviewError("");
    api<AccountNote[]>(
      `/accounts/${accountId}/notes?limit=5`,
      "GET",
      undefined,
      { signal: controller.signal },
    )
      .then((notes) => {
        if (!controller.signal.aborted) setPreview(notes);
      })
      .catch((error) => {
        if (!controller.signal.aborted) setPreviewError(error.message);
      });
    return () => controller.abort();
  }, [hover, open, accountId, epoch]);
  const launch = () => {
    setHover(false);
    setOpen(true);
  };
  return (
    <>
      {entry ? (
        <Button onClick={launch}>添加备注</Button>
      ) : (
        count > 0 && (
          <Popover
            trigger={["hover", "focus"]}
            open={hover && !open}
            onOpenChange={setHover}
            title="最近 5 条备注"
            mouseEnterDelay={0.2}
            content={
              <div className="account-notes-preview">
                {previewError ? (
                  <Alert type="error" message={previewError} />
                ) : preview ? (
                  <NoteList notes={preview} />
                ) : (
                  <Spin size="small" />
                )}
                <Button type="link" onClick={launch}>
                  查看全部备注
                </Button>
              </div>
            }
          >
            <Button
              type="text"
              size="small"
              className="account-notes-icon"
              icon={<FileTextOutlined />}
              aria-label={`查看账号备注（${count}条）`}
              onClick={launch}
            />
          </Popover>
        )
      )}
      <Drawer
        title="账号情况备注"
        open={open}
        onClose={() => setOpen(false)}
        width={480}
        destroyOnHidden
      >
        {open && (
          <NotesPanel
            key={accountId}
            accountId={accountId}
            epoch={epoch}
            refresh={refresh}
            canAdd={canAdd}
          />
        )}
      </Drawer>
    </>
  );
}

function NotesPanel({
  accountId,
  epoch,
  refresh,
  canAdd,
}: {
  accountId: string;
  epoch: number;
  refresh: () => void;
  canAdd: boolean;
}) {
  const { message } = App.useApp();
  const [form] = Form.useForm();
  const [notes, setNotes] = useState<AccountNote[]>([]);
  const [loading, setLoading] = useState(true),
    [saving, setSaving] = useState(false);
  const [error, setError] = useState(""),
    [denied, setDenied] = useState(false);
  const [more, setMore] = useState(false),
    [revision, setRevision] = useState(0);
  const generation = useRef(0);
  const paging = useRef(false);
  // Revalidation also clears historical content when access is withdrawn.
  useEffect(() => {
    const current = ++generation.current;
    const controller = new AbortController();
    setLoading(true);
    setError("");
    paging.current = false;
    api<AccountNote[]>(
      `/accounts/${accountId}/notes?limit=50`,
      "GET",
      undefined,
      { signal: controller.signal },
    )
      .then((data) => {
        if (current === generation.current && !controller.signal.aborted) {
          setNotes((existing) => {
            const merged = new Map(existing.map((note) => [note.id, note]));
            for (const note of data) merged.set(note.id, note);
            return [...merged.values()].sort((a, b) => b.id - a.id);
          });
          setMore(data.length === 50 || notes.length >= 50);
          setDenied(false);
        }
      })
      .catch((e) => {
        if (current === generation.current && !controller.signal.aborted) {
          setError(e.message);
          if (e instanceof ApiError && [401, 403, 404].includes(e.status)) {
            setNotes([]);
            setMore(false);
            setDenied(true);
            form.resetFields();
          }
        }
      })
      .finally(() => {
        if (current === generation.current && !controller.signal.aborted)
          setLoading(false);
      });
    return () => {
      controller.abort();
      generation.current++;
    };
  }, [accountId, epoch, revision, form]);
  async function loadMore() {
    if (paging.current || loading || !more) return;
    paging.current = true;
    setLoading(true);
    setError("");
    const current = generation.current;
    try {
      const data = await api<AccountNote[]>(
        `/accounts/${accountId}/notes?limit=50&before=${notes.at(-1)!.id}`,
      );
      if (current === generation.current) {
        setNotes((existing) => [...existing, ...data]);
        setMore(data.length === 50);
      }
    } catch (e) {
      if (current === generation.current) {
        setError((e as Error).message);
        if (e instanceof ApiError && [401, 403, 404].includes(e.status)) {
          setNotes([]);
          setMore(false);
          setDenied(true);
          form.resetFields();
        }
      }
    } finally {
      if (current === generation.current) {
        paging.current = false;
        setLoading(false);
      }
    }
  }
  async function submit({ content }: { content: string }) {
    if (saving) return;
    setSaving(true);
    const session = getSessionVersion();
    try {
      await api(`/accounts/${accountId}/notes`, "POST", {
        content: content.trim(),
      });
      if (session !== getSessionVersion()) return;
      form.resetFields();
      setRevision((v) => v + 1);
      refresh();
      message.success("备注已登记，历史记录不可修改或删除");
    } catch (e) {
      if (session !== getSessionVersion()) return;
      message.error((e as Error).message);
      if (e instanceof ApiError && [401, 403, 404].includes(e.status)) {
        setDenied(true);
        form.resetFields();
        refresh();
        setRevision((v) => v + 1);
      }
    } finally {
      setSaving(false);
    }
  }
  return (
    <div className="account-notes-panel">
      <p className="small muted">
        备注仅可新增，不能修改或删除。登记时间为北京时间。
      </p>
      {canAdd && !denied && (
        <Form form={form} layout="vertical" onFinish={submit}>
          <Form.Item
            name="content"
            label="新增备注"
            rules={[
              {
                validator: async (_, value) => {
                  const length = Array.from((value || "").trim()).length;
                  if (!length || length > 200)
                    throw new Error("请输入 1–200 字的备注");
                },
              },
            ]}
          >
            <Input.TextArea
              autoSize={{ minRows: 3, maxRows: 6 }}
              count={{
                show: true,
                max: 200,
                strategy: (value) => Array.from(value).length,
              }}
              placeholder="记录账号的使用情况，最多 200 字"
            />
          </Form.Item>
          <Button
            type="primary"
            htmlType="submit"
            loading={saving}
            disabled={loading}
          >
            登记备注
          </Button>
        </Form>
      )}
      {error && (
        <Alert
          type="error"
          message={error}
          action={
            <Button size="small" onClick={() => setRevision((v) => v + 1)}>
              重试
            </Button>
          }
        />
      )}
      <NoteList notes={notes} />
      {loading && <Spin size="small" />}
      {!loading && !error && !notes.length && (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无备注" />
      )}
      {more && (
        <Button loading={loading} onClick={loadMore}>
          加载更多
        </Button>
      )}
    </div>
  );
}
