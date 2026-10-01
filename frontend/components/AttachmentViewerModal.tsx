"use client";

import { useEffect, useState } from "react";
import {
  AttachmentKind,
  AttachmentTable,
  attachmentFileUrl,
  fetchAttachmentPreviewPdf,
  getAttachmentTable,
  getAttachmentText,
} from "@/lib/api";
import MarkdownContent from "./MarkdownContent";

export interface ViewerAttachment {
  id: string;
  original_name: string;
  mime_type: string;
  size_bytes: number;
}

function extOf(name: string): string {
  const idx = name.lastIndexOf(".");
  return idx >= 0 ? name.slice(idx + 1).toLowerCase() : "";
}

type ViewerKind = "image" | "pdf" | "txt" | "md" | "table" | "docx-pptx" | "hwp" | "unsupported";

function viewerKind(mimeType: string, filename: string): ViewerKind {
  // application/pdf는 실제 PDF 업로드뿐 아니라, 스캔본 폴백으로 변환된
  // docx/pptx/hwp도 포함한다 - 모델이 실제로 본 파일과 같은 걸 보여줘야 하므로
  // 확장자보다 mime_type을 우선 검사한다.
  if (mimeType.startsWith("image/")) return "image";
  if (mimeType === "application/pdf") return "pdf";

  const ext = extOf(filename);
  if (ext === "txt") return "txt";
  if (ext === "md") return "md";
  if (ext === "csv" || ext === "xlsx") return "table";
  if (ext === "docx" || ext === "pptx") return "docx-pptx";
  if (ext === "hwp" || ext === "hwpx") return "hwp";
  return "unsupported";
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
}

function CloseIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="h-4 w-4">
      <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

function DownloadIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="h-4 w-4">
      <path
        d="M12 3v12m0 0l-4-4m4 4l4-4M5 19h14"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function Spinner() {
  return (
    <div className="flex flex-col items-center gap-2 text-sm text-gray-400 dark:text-gray-500">
      <div className="h-6 w-6 animate-spin rounded-full border-2 border-gray-300 border-t-gray-500 dark:border-gray-700 dark:border-t-gray-400" />
      <span>불러오는 중...</span>
    </div>
  );
}

function ErrorMessage({
  message,
  kind,
  parentId,
  attachment,
}: {
  message: string;
  kind: AttachmentKind;
  parentId: string;
  attachment: ViewerAttachment;
}) {
  return (
    <div className="flex flex-col items-center gap-3 text-center text-sm text-gray-500 dark:text-gray-400">
      <p>{message}</p>
      <a
        href={attachmentFileUrl(kind, parentId, attachment.id, true)}
        className="flex items-center gap-1.5 rounded-lg border border-gray-300 px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50 dark:border-gray-600 dark:text-gray-200 dark:hover:bg-gray-800"
      >
        <DownloadIcon />
        다운로드
      </a>
    </div>
  );
}

function TableView({ table }: { table: AttachmentTable }) {
  const [activeSheet, setActiveSheet] = useState(0);
  const sheet = table.sheets[activeSheet];
  if (!sheet) return null;

  const [header, ...body] = sheet.rows;

  return (
    <div className="flex h-full flex-col">
      {table.sheets.length > 1 && (
        <div className="mb-2 flex shrink-0 flex-wrap gap-1.5 border-b border-gray-100 pb-2 dark:border-gray-800">
          {table.sheets.map((s, i) => (
            <button
              key={s.name}
              type="button"
              onClick={() => setActiveSheet(i)}
              className={`rounded-lg px-2.5 py-1 text-xs font-medium ${
                i === activeSheet
                  ? "bg-gray-900 text-white dark:bg-gray-100 dark:text-gray-900"
                  : "bg-gray-100 text-gray-600 hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-300 dark:hover:bg-gray-700"
              }`}
            >
              {s.name}
            </button>
          ))}
        </div>
      )}

      {sheet.truncated && (
        <p className="mb-2 shrink-0 text-xs text-gray-400 dark:text-gray-500">
          전체 {sheet.total_rows}행 중 앞부분만 표시됩니다.
        </p>
      )}

      <div className="min-h-0 flex-1 overflow-auto rounded-lg border border-gray-200 dark:border-gray-700">
        <table className="w-full border-collapse text-sm">
          {header && (
            <thead className="sticky top-0 bg-gray-50 dark:bg-gray-800">
              <tr>
                {header.map((cell, i) => (
                  <th
                    key={i}
                    className="whitespace-nowrap border-b border-gray-200 px-3 py-1.5 text-left font-semibold text-gray-700 dark:border-gray-700 dark:text-gray-200"
                  >
                    {cell}
                  </th>
                ))}
              </tr>
            </thead>
          )}
          <tbody>
            {body.map((row, i) => (
              <tr key={i} className="even:bg-gray-50/50 dark:even:bg-gray-900/40">
                {row.map((cell, j) => (
                  <td
                    key={j}
                    className="whitespace-nowrap border-b border-gray-100 px-3 py-1.5 text-gray-700 dark:border-gray-800 dark:text-gray-300"
                  >
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ViewerBody({
  kind,
  parentId,
  attachment,
}: {
  kind: AttachmentKind;
  parentId: string;
  attachment: ViewerAttachment;
}) {
  const viewKind = viewerKind(attachment.mime_type, attachment.original_name);
  const [loading, setLoading] = useState(viewKind !== "image" && viewKind !== "unsupported");
  const [error, setError] = useState<string | null>(null);
  const [text, setText] = useState<{ text: string; truncated: boolean } | null>(null);
  const [table, setTable] = useState<AttachmentTable | null>(null);
  const [pdfBlobUrl, setPdfBlobUrl] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let createdBlobUrl: string | null = null;

    async function load() {
      try {
        if (viewKind === "txt" || viewKind === "md" || viewKind === "hwp") {
          const result = await getAttachmentText(kind, parentId, attachment.id);
          if (!cancelled) setText(result);
        } else if (viewKind === "table") {
          const result = await getAttachmentTable(kind, parentId, attachment.id);
          if (!cancelled) setTable(result);
        } else if (viewKind === "docx-pptx") {
          const url = await fetchAttachmentPreviewPdf(kind, parentId, attachment.id);
          createdBlobUrl = url;
          if (!cancelled) setPdfBlobUrl(url);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "미리보기를 불러오지 못했습니다.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    if (viewKind === "image" || viewKind === "pdf" || viewKind === "unsupported") {
      setLoading(false);
    } else {
      load();
    }

    return () => {
      cancelled = true;
      if (createdBlobUrl) URL.revokeObjectURL(createdBlobUrl);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind, parentId, attachment.id, viewKind]);

  if (viewKind === "image") {
    return (
      <div className="flex h-full items-center justify-center overflow-auto">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={attachmentFileUrl(kind, parentId, attachment.id)}
          alt={attachment.original_name}
          className="max-h-full max-w-full object-contain"
        />
      </div>
    );
  }

  if (viewKind === "pdf") {
    return (
      <iframe
        src={attachmentFileUrl(kind, parentId, attachment.id)}
        title={attachment.original_name}
        className="h-full w-full rounded-lg border border-gray-200 dark:border-gray-700"
      />
    );
  }

  if (viewKind === "unsupported") {
    return (
      <div className="flex h-full items-center justify-center">
        <ErrorMessage
          message="미리보기를 지원하지 않는 형식입니다."
          kind={kind}
          parentId={parentId}
          attachment={attachment}
        />
      </div>
    );
  }

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        {viewKind === "docx-pptx" ? (
          <div className="flex flex-col items-center gap-2 text-sm text-gray-400 dark:text-gray-500">
            <div className="h-6 w-6 animate-spin rounded-full border-2 border-gray-300 border-t-gray-500 dark:border-gray-700 dark:border-t-gray-400" />
            <span>변환 중...</span>
          </div>
        ) : (
          <Spinner />
        )}
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex h-full items-center justify-center">
        <ErrorMessage message={error} kind={kind} parentId={parentId} attachment={attachment} />
      </div>
    );
  }

  if (viewKind === "docx-pptx" && pdfBlobUrl) {
    return (
      <iframe
        src={pdfBlobUrl}
        title={attachment.original_name}
        className="h-full w-full rounded-lg border border-gray-200 dark:border-gray-700"
      />
    );
  }

  if (viewKind === "txt" && text) {
    return (
      <div className="h-full overflow-auto rounded-lg border border-gray-200 p-3 dark:border-gray-700">
        <pre className="whitespace-pre-wrap break-words font-mono text-xs text-gray-800 dark:text-gray-200">
          {text.text}
        </pre>
        {text.truncated && (
          <p className="mt-2 text-xs text-gray-400 dark:text-gray-500">
            파일이 커서 앞부분만 표시됩니다. 전체 내용은 다운로드해서 확인하세요.
          </p>
        )}
      </div>
    );
  }

  if (viewKind === "md" && text) {
    return (
      <div className="h-full overflow-auto rounded-lg border border-gray-200 p-4 dark:border-gray-700">
        <MarkdownContent content={text.text} />
        {text.truncated && (
          <p className="mt-2 text-xs text-gray-400 dark:text-gray-500">
            파일이 커서 앞부분만 표시됩니다. 전체 내용은 다운로드해서 확인하세요.
          </p>
        )}
      </div>
    );
  }

  if (viewKind === "hwp" && text) {
    return (
      <div className="flex h-full flex-col gap-2">
        <p className="shrink-0 text-xs text-gray-400 dark:text-gray-500">
          텍스트만 표시됩니다. 원본 모양은 다운로드해서 확인하세요.
        </p>
        <div className="min-h-0 flex-1 overflow-auto rounded-lg border border-gray-200 p-3 dark:border-gray-700">
          <pre className="whitespace-pre-wrap break-words font-mono text-xs text-gray-800 dark:text-gray-200">
            {text.text}
          </pre>
        </div>
      </div>
    );
  }

  if (viewKind === "table" && table) {
    return <TableView table={table} />;
  }

  return null;
}

export default function AttachmentViewerModal({
  kind,
  parentId,
  attachment,
  onClose,
}: {
  kind: AttachmentKind; // "chat"이면 채팅 첨부(/conversations/...), "project"면 참고자료(/projects/...)
  parentId: string; // kind에 따라 conversationId 또는 projectId
  attachment: ViewerAttachment;
  onClose: () => void;
}) {
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
    >
      <div
        className="flex h-[85vh] w-full max-w-4xl flex-col overflow-hidden rounded-2xl bg-white shadow-xl dark:bg-gray-900"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex shrink-0 items-center justify-between gap-3 border-b border-gray-100 px-4 py-3 dark:border-gray-800">
          <div className="min-w-0">
            <p className="truncate text-sm font-medium text-gray-900 dark:text-gray-100" title={attachment.original_name}>
              {attachment.original_name}
            </p>
            <p className="text-xs text-gray-400 dark:text-gray-500">{formatBytes(attachment.size_bytes)}</p>
          </div>
          <div className="flex shrink-0 items-center gap-1.5">
            <a
              href={attachmentFileUrl(kind, parentId, attachment.id, true)}
              aria-label="다운로드"
              className="flex h-8 w-8 items-center justify-center rounded-lg text-gray-400 hover:bg-gray-100 hover:text-gray-700 dark:hover:bg-gray-800 dark:hover:text-gray-200"
            >
              <DownloadIcon />
            </a>
            <button
              type="button"
              onClick={onClose}
              aria-label="닫기"
              className="flex h-8 w-8 items-center justify-center rounded-lg text-gray-400 hover:bg-gray-100 hover:text-gray-700 dark:hover:bg-gray-800 dark:hover:text-gray-200"
            >
              <CloseIcon />
            </button>
          </div>
        </div>

        <div className="min-h-0 flex-1 p-4">
          <ViewerBody kind={kind} parentId={parentId} attachment={attachment} />
        </div>
      </div>
    </div>
  );
}
