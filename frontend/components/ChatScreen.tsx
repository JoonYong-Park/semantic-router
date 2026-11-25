"use client";

// "/" (새 채팅)와 "/c/[id]" (기존 채팅) 양쪽이 똑같이 쓰는 화면 껍데기 -
// 파일 드래그앤드롭 오버레이 + 입력창(첨부/모델피커/전송)을 여기 하나로
// 묶어서, 두 라우트가 완전히 같은 로직을 각자 복붙해 들고 있지 않게 한다.
// 메시지 목록(또는 EmptyState) 부분만 children으로 받는다.

import {
  DragEvent,
  KeyboardEvent,
  ReactNode,
  useEffect,
  useRef,
  useState,
} from "react";
import ModelPicker from "@/components/ModelPicker";
import UsageRing from "@/components/UsageRing";
import { AttachmentChip } from "@/components/AttachmentChip";
import { isGuest } from "@/lib/guest";
import { Mode, PendingAttachment, Size } from "@/lib/types";

// 서버(attachments.py ALL_ALLOWED_EXTS)와 맞춰둔 허용 확장자 - <input accept>와
// 드롭 시 1차 필터링용. 최종 검증은 항상 백엔드가 한다.
const ACCEPTED_EXTENSIONS = [
  ".txt", ".md", ".csv", ".xlsx", ".docx", ".pptx",
  ".hwp", ".hwpx", ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp",
];

export default function ChatScreen({
  children,
  input,
  onInputChange,
  onSend,
  disabled,
  attachments,
  onFilesSelected,
  onRemoveAttachment,
  onAttachmentChipClick,
  mode,
  size,
  onModelSelect,
}: {
  children: ReactNode;
  input: string;
  onInputChange: (value: string) => void;
  onSend: () => void;
  disabled: boolean; // 스트리밍 중이거나 업로드 중 - 전송/첨부 버튼 비활성화
  attachments: PendingAttachment[];
  onFilesSelected: (files: FileList | File[]) => void;
  onRemoveAttachment: (attachment: PendingAttachment) => void;
  onAttachmentChipClick: (attachment: PendingAttachment) => void;
  mode: Mode;
  size: Size;
  onModelSelect: (mode: Mode, size: Size) => void;
}) {
  const [isDraggingFile, setIsDraggingFile] = useState(false);
  const dragDepthRef = useRef(0);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  // e.nativeEvent.isComposing 하나만으로는 브라우저별로 놓치는 경우가 있어서,
  // onCompositionStart/End로 직접 추적하는 걸 같이 둔다 (표준적인 이중 방어).
  const isComposingRef = useRef(false);

  const isUploadingAttachment = attachments.some((a) => a.status === "uploading");
  // 게스트 모드는 읽기 전용 - 입력·첨부·전송을 막는다 (AuthGate가 로그인 상태를 정한 뒤에만 렌더링된다).
  const guest = isGuest();

  // 입력창 자동 높이 조절 (Shift+Enter 줄바꿈 시 늘어나도록)
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [input]);

  function handleDragEnter(e: DragEvent<HTMLDivElement>) {
    if (!e.dataTransfer.types.includes("Files")) return;
    e.preventDefault();
    dragDepthRef.current += 1;
    setIsDraggingFile(true);
  }

  function handleDragOver(e: DragEvent<HTMLDivElement>) {
    if (!e.dataTransfer.types.includes("Files")) return;
    e.preventDefault();
  }

  function handleDragLeave(e: DragEvent<HTMLDivElement>) {
    if (!e.dataTransfer.types.includes("Files")) return;
    e.preventDefault();
    dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
    if (dragDepthRef.current === 0) setIsDraggingFile(false);
  }

  function handleDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    dragDepthRef.current = 0;
    setIsDraggingFile(false);
    if (e.dataTransfer.files.length > 0) {
      onFilesSelected(e.dataTransfer.files);
    }
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      // 한글/일본어 등 IME로 글자를 조합하는 도중에 Enter를 누르면(조합 확정용),
      // 브라우저가 이 keydown을 isComposing=true로 먼저 한 번 보내고 조금 뒤에
      // 진짜 Enter를 한 번 더 보낸다. 이걸 구분 안 하면 "조합 중이던 일부 글자"로
      // 한 번, "완성된 전체 문장"으로 또 한 번 - 총 두 번 전송돼버린다.
      if (e.nativeEvent.isComposing || isComposingRef.current) return;
      e.preventDefault();
      onSend();
    }
  }

  const readyCount = attachments.filter((a) => a.status === "ready").length;

  return (
    <div
      className="relative flex min-w-0 flex-1 flex-col bg-white dark:bg-gray-950"
      onDragEnter={handleDragEnter}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      {isDraggingFile && (
        <div className="pointer-events-none absolute inset-0 z-10 m-3 flex items-center justify-center rounded-2xl border-2 border-dashed border-gray-400 bg-white/80 text-sm font-medium text-gray-600 dark:border-gray-500 dark:bg-gray-950/80 dark:text-gray-300">
          여기에 파일을 놓으세요
        </div>
      )}

      {children}

      <div className="border-t border-gray-100 bg-white px-4 py-4 dark:border-gray-800 dark:bg-gray-950">
        <div className="mx-auto max-w-2xl">
          {attachments.length > 0 && (
            <div className="mb-2 flex flex-wrap gap-2">
              {attachments.map((a) => (
                <AttachmentChip
                  key={a.id}
                  name={a.file.name}
                  progress={a.status === "uploading" ? a.progress ?? 0 : undefined}
                  error={a.status === "error" ? a.error ?? "업로드 실패" : undefined}
                  onRemove={() => onRemoveAttachment(a)}
                  onClick={a.status === "ready" ? () => onAttachmentChipClick(a) : undefined}
                />
              ))}
            </div>
          )}

          <div className="flex items-end gap-2 rounded-3xl border border-gray-300 bg-white px-2 py-1.5 shadow-sm focus-within:border-gray-400 dark:border-gray-700 dark:bg-gray-900 dark:focus-within:border-gray-500">
            <UsageRing />

            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept={ACCEPTED_EXTENSIONS.join(",")}
              className="sr-only"
              onChange={(e) => {
                // e.target.files는 input에 실시간으로 묶인 라이브 FileList라,
                // onFilesSelected(비동기 - 내부에 await 지점이 있음)에 그대로
                // 넘기고 나서 바로 아래에서 value를 리셋하면, 그 await가 끝나기도
                // 전에 라이브 FileList가 먼저 비어버려서 실제로는 파일이 0개로
                // 처리된다. 그래서 File 객체들을 여기서 동기적으로 배열로 미리
                // 꺼내놓은 뒤에 value를 리셋한다.
                const selected = e.target.files ? Array.from(e.target.files) : [];
                e.target.value = "";
                if (selected.length > 0) {
                  onFilesSelected(selected);
                }
              }}
            />
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={guest}
              aria-label="파일 첨부"
              className="mb-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-gray-500 hover:bg-gray-100 disabled:opacity-30 dark:text-gray-400 dark:hover:bg-gray-800"
            >
              <svg viewBox="0 0 24 24" fill="none" className="h-4 w-4">
                <path
                  d="M21.44 11.05l-9.19 9.19a5 5 0 01-7.07-7.07l9.19-9.19a3.33 3.33 0 014.71 4.71l-9.2 9.19a1.67 1.67 0 01-2.36-2.36l8.49-8.48"
                  stroke="currentColor"
                  strokeWidth="1.8"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </button>

            <textarea
              ref={textareaRef}
              rows={1}
              className="max-h-40 min-w-0 flex-1 resize-none overflow-y-auto bg-transparent px-3 py-2 text-sm text-gray-900 outline-none placeholder:overflow-hidden placeholder:whitespace-nowrap placeholder:text-gray-400 dark:text-gray-100 dark:placeholder:text-gray-500"
              placeholder={
                guest
                  ? "게스트 모드에서는 질문할 수 없어요. 로그인하면 바로 사용할 수 있어요."
                  : "메시지를 입력하세요... (Shift+Enter로 줄바꿈)"
              }
              disabled={guest}
              value={input}
              onChange={(e) => onInputChange(e.target.value)}
              onKeyDown={handleKeyDown}
              onCompositionStart={() => {
                isComposingRef.current = true;
              }}
              onCompositionEnd={() => {
                isComposingRef.current = false;
              }}
            />

            <ModelPicker mode={mode} size={size} onSelect={onModelSelect} />

            <button
              type="button"
              onClick={onSend}
              disabled={guest || disabled || isUploadingAttachment || (!input.trim() && readyCount === 0)}
              aria-label="전송"
              className="mb-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-gray-900 text-white disabled:opacity-30 dark:bg-white dark:text-gray-900"
            >
              <svg viewBox="0 0 24 24" fill="none" className="h-4 w-4">
                <path
                  d="M12 19V5M12 5L6 11M12 5L18 11"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
