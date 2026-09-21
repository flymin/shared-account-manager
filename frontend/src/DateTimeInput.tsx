import { useId, useLayoutEffect, useRef, useState } from "react";
import {
  Button,
  Calendar,
  ConfigProvider,
  Input,
  Popover,
  type InputRef,
} from "antd";
import { CalendarOutlined, CloseCircleFilled } from "@ant-design/icons";
import dayjs, { type Dayjs } from "dayjs";
import "dayjs/locale/zh-cn";
dayjs.locale("zh-cn");

export const dateTimeRule = {
  validator: (_: unknown, value?: string) => {
    if (
      !value ||
      (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value) &&
        dayjs(value).format("YYYY-MM-DDTHH:mm") === value)
    )
      return Promise.resolve();
    return Promise.reject(
      new Error("请输入有效日期与时间（小时 00–23，分钟 00–59）"),
    );
  },
};

const ROW_HEIGHT = 36;
const pad = (value: number) => String(value).padStart(2, "0");

function TimeColumn({
  part,
  value,
  onChange,
}: {
  part: "hour" | "minute";
  value: number;
  onChange: (value: number) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const selected = useRef<number | undefined>(undefined);
  const latestValue = useRef(value);
  latestValue.current = value;
  const id = useId();
  const maximum = part === "hour" ? 23 : 59;
  const clamp = (next: number) => Math.max(0, Math.min(maximum, next));
  useLayoutEffect(() => {
    const column = ref.current!;
    const observer = new ResizeObserver(initialize);
    function initialize() {
      // Popover portals can mount before becoming measurable. Initialize the
      // position only after layout, otherwise the browser clamps it to zero.
      if (column.clientHeight) {
        if (selected.current === undefined) {
          column.scrollTop = latestValue.current * ROW_HEIGHT;
          selected.current = latestValue.current;
        }
        observer.disconnect();
      }
    }
    initialize();
    if (selected.current === undefined) observer.observe(column);
    return () => observer.disconnect();
  }, []);
  useLayoutEffect(() => {
    // Native scroll owns inertia and snapping. Only an external value change
    // may reposition an initialized column; no observer runs during a gesture.
    if (selected.current !== undefined && selected.current !== value) {
      ref.current!.scrollTop = value * ROW_HEIGHT;
      selected.current = value;
    }
  }, [value]);
  function select(next: number) {
    const bounded = clamp(next);
    selected.current = bounded;
    ref.current!.scrollTop = bounded * ROW_HEIGHT;
    onChange(bounded);
  }
  return (
    <div className="time-column-wrap">
      <span className="time-column-label">{part === "hour" ? "时" : "分"}</span>
      <div className="time-wheel-frame">
        <div
          ref={ref}
          className="time-column"
          data-type={part}
          role="listbox"
          aria-label={part === "hour" ? "小时" : "分钟"}
          aria-activedescendant={`${id}-${value}`}
          tabIndex={0}
          onScroll={(event) => {
            if (selected.current === undefined) return;
            const next = clamp(
              Math.round(event.currentTarget.scrollTop / ROW_HEIGHT),
            );
            if (next !== selected.current) {
              selected.current = next;
              onChange(next);
            }
          }}
          onKeyDown={(event) => {
            const current = selected.current ?? value;
            const next = {
              ArrowUp: current - 1,
              ArrowDown: current + 1,
              PageUp: current - 5,
              PageDown: current + 5,
              Home: 0,
              End: maximum,
            }[event.key];
            if (next !== undefined) {
              event.preventDefault();
              select(next);
            }
          }}
        >
          {Array.from({ length: maximum + 1 }, (_, index) => (
            <div
              key={index}
              id={`${id}-${index}`}
              role="option"
              aria-selected={index === value}
              className="time-column-option"
              onClick={() => {
                select(index);
                ref.current!.focus({ preventScroll: true });
              }}
            >
              {pad(index)}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

export function DateTimeInput({
  value,
  onChange,
  ...props
}: {
  value?: string;
  onChange?: (value: string | undefined) => void;
  id?: string;
  disabled?: boolean;
  "aria-describedby"?: string;
  "aria-invalid"?: boolean;
  "aria-required"?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [opening, setOpening] = useState(0);
  const [draft, setDraft] = useState<Dayjs>(() => dayjs(value));
  const [viewDate, setViewDate] = useState(draft);
  const draftRef = useRef(draft);
  const inputRef = useRef<InputRef>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const keyboardOpen = useRef(false);
  const popupId = useId();
  function updateDraft(update: (current: Dayjs) => Dayjs) {
    draftRef.current = update(draftRef.current);
    setDraft(draftRef.current);
  }
  function changeOpen(next: boolean) {
    if (props.disabled) return;
    if (next && !open) {
      const today = new Intl.DateTimeFormat("sv-SE", {
        timeZone: "Asia/Shanghai",
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
      }).format(new Date());
      const initial =
        value && dayjs(value).isValid()
          ? dayjs(value)
          : dayjs(`${today}T00:00`);
      updateDraft(() => initial);
      setViewDate(initial);
      setOpening((current) => current + 1);
    }
    if (!next) keyboardOpen.current = false;
    setOpen(next);
  }
  function close() {
    keyboardOpen.current = false;
    setOpen(false);
    inputRef.current?.focus({ preventScroll: true });
  }
  function confirm() {
    onChange?.(draftRef.current.format("YYYY-MM-DDTHH:mm"));
    close();
  }
  return (
    <Popover
      open={open && !props.disabled}
      onOpenChange={changeOpen}
      trigger="click"
      placement="bottomLeft"
      arrow={false}
      destroyOnHidden
      afterOpenChange={(visible) => {
        if (visible && keyboardOpen.current) {
          panelRef.current
            ?.querySelector<HTMLElement>("[role=listbox]")
            ?.focus();
          keyboardOpen.current = false;
        }
      }}
      classNames={{ root: "bounded-date-time-popup" }}
      content={
        <div
          ref={panelRef}
          id={popupId}
          className="date-time-panel"
          role="dialog"
          aria-label="选择日期和时间"
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              event.preventDefault();
              event.stopPropagation();
              close();
            } else if (
              event.key === "Enter" &&
              (event.target as HTMLElement).getAttribute("role") === "listbox"
            ) {
              event.preventDefault();
              confirm();
            }
          }}
        >
          <div className="date-time-panel-header">
            <strong>{draft.format("YYYY-MM-DD HH:mm")}</strong>
            <span>北京时间 · 24 小时制</span>
          </div>
          <div className="date-time-panel-body">
            <ConfigProvider
              theme={{ token: { motion: false } }}
              getPopupContainer={(trigger) =>
                trigger?.closest<HTMLElement>(".bounded-date-time-popup") ||
                document.body
              }
            >
              <Calendar
                fullscreen={false}
                mode="month"
                value={viewDate}
                onChange={setViewDate}
                onSelect={(date, info) => {
                  if (info.source === "date")
                    updateDraft((current) =>
                      dayjs(
                        `${date.format("YYYY-MM-DD")}T${current.format("HH:mm")}`,
                      ),
                    );
                }}
              />
            </ConfigProvider>
            <div className="time-columns">
              {(["hour", "minute"] as const).map((part) => (
                <TimeColumn
                  key={`${opening}-${part}`}
                  part={part}
                  value={draft[part]()}
                  onChange={(next) => {
                    if (open) updateDraft((current) => current[part](next));
                  }}
                />
              ))}
            </div>
          </div>
          <div className="date-time-panel-footer">
            <span>滚动或点选时分</span>
            <Button onClick={close}>取消</Button>
            <Button type="primary" onClick={confirm}>
              确定
            </Button>
          </div>
        </div>
      }
    >
      <Input
        {...props}
        ref={inputRef}
        readOnly
        value={value ? dayjs(value).format("YYYY-MM-DD HH:mm") : ""}
        placeholder="选择日期和时间"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? popupId : undefined}
        onKeyDown={(event) => {
          if (["Enter", " ", "ArrowDown"].includes(event.key)) {
            event.preventDefault();
            keyboardOpen.current = true;
            changeOpen(true);
            if (open)
              panelRef.current
                ?.querySelector<HTMLElement>("[role=listbox]")
                ?.focus();
          } else if (event.key === "Escape" && open) {
            event.stopPropagation();
            close();
          }
        }}
        suffix={
          <>
            {value && !props.disabled && (
              <button
                type="button"
                className="date-time-clear"
                aria-label="清除日期和时间"
                onClick={(event) => {
                  event.stopPropagation();
                  onChange?.(undefined);
                  close();
                }}
              >
                <CloseCircleFilled aria-hidden="true" />
              </button>
            )}
            <CalendarOutlined aria-hidden="true" />
          </>
        }
        className="date-time-input"
      />
    </Popover>
  );
}
