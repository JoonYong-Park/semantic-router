// "새 채팅 시작" 화면. 실제 대화방(row)은 아직 없는 상태 - 사용자가 첫
// 메시지를 보내는(또는 첫 파일을 첨부하는) 순간에야 서버에 채팅방이
// 생성되고, 그 즉시 /c/{id}로 이동한다.
//
// 스트리밍/메시지 목록 로직은 여기 없다 - "/"에서는 절대 스트리밍이 시작되지
// 않는다(첫 메시지를 만들자마자 /c/{id}로 넘겨서, 실제 전송·SSE 수신은 그
// 페이지가 담당). 그래서 이 페이지는 "대화방을 만들고 첫 메시지를 보낼
// 준비물(내용/첨부)을 챙겨서 다음 페이지에 넘겨주는" 역할만 한다.
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router";
import ChatScreen from "@/components/ChatScreen";
import EmptyState from "@/components/EmptyState";
import { useAppData } from "@/contexts/AppDataContext";
import { createConversation, deleteAttachment, uploadAttachment } from "@/lib/api";
import { Mode, PendingAttachment, Size } from "@/lib/types";
import { stashPendingMessage } from "@/lib/pendingMessage";

import { DEFAULT_MODE } from "@/lib/config";
let idCounter = 0;
const nextId = () => `${Date.now()}-${idCounter++}`;

export default function Home() {
  const navigate = useNavigate();
  const { setConversations, setIsStreaming } = useAppData();

  const [mode, setMode] = useState<Mode>(DEFAULT_MODE);
  const [size, setSize] = useState<Size>("medium");
  const [input, setInput] = useState("");
  const [attachments, setAttachments] = useState<PendingAttachment[]>([]);
  const isUploadingAttachment = attachments.some((a) => a.status === "uploading");

  // 파일을 먼저 드래그해서 첨부하면(아직 텍스트도 안 쳤고 전송도 안 누른
  // 시점) 그 시점에 이미 서버에 채팅방을 만들어야 업로드 대상 chat_id가
  // 생긴다. 다만 이때는 아직 URL을 안 바꾼다(/c/{id}로 넘어가는 건 실제
  // "전송"을 누른 순간으로 미뤄둠) - 그래야 이 화면의 attachments state가
  // 유지된 채로 사용자가 계속 타이핑할 수 있다.
  const pendingConvIdRef = useRef<string | null>(null);

  // 이 화면에 있는 한 스트리밍 중일 수 없다(전송 즉시 /c/{id}로 넘어가므로).
  // 다른 라우트에서 true로 남겨뒀을 가능성에 대비해 마운트 시 확실히 초기화.
  useEffect(() => {
    setIsStreaming(false);
  }, [setIsStreaming]);

  async function ensureConversationId(): Promise<string | null> {
    if (pendingConvIdRef.current) return pendingConvIdRef.current;
    try {
      const conv = await createConversation();
      pendingConvIdRef.current = conv.id;
      setConversations((prev) => [conv, ...prev]);
      return conv.id;
    } catch {
      return null;
    }
  }

  async function handleFilesSelected(files: FileList | File[]) {
    const convId = await ensureConversationId();
    if (!convId) return;

    for (const file of Array.from(files)) {
      const localId = nextId();
      setAttachments((prev) => [...prev, { id: localId, file, status: "uploading", progress: 0 }]);
      try {
        const uploaded = await uploadAttachment(convId, file, (percent) => {
          setAttachments((prev) =>
            prev.map((a) => (a.id === localId ? { ...a, progress: percent } : a))
          );
        });
        setAttachments((prev) =>
          prev.map((a) => (a.id === localId ? { ...a, id: uploaded.id, status: "ready" } : a))
        );
      } catch (err) {
        setAttachments((prev) =>
          prev.map((a) =>
            a.id === localId
              ? { ...a, status: "error", error: err instanceof Error ? err.message : "업로드 실패" }
              : a
          )
        );
      }
    }
  }

  function handleRemoveAttachment(attachment: PendingAttachment) {
    setAttachments((prev) => prev.filter((a) => a.id !== attachment.id));
    if (attachment.status === "ready" && pendingConvIdRef.current) {
      deleteAttachment(pendingConvIdRef.current, attachment.id).catch(() => {
        // 취소 실패해도 UI에서는 이미 지웠으니 조용히 무시 (데모 범위)
      });
    }
  }

  async function handleSend() {
    const text = input.trim();
    const readyAttachments = attachments.filter((a) => a.status === "ready");
    if ((!text && readyAttachments.length === 0) || isUploadingAttachment) return;

    let convId = pendingConvIdRef.current;
    if (!convId) {
      try {
        const conv = await createConversation();
        convId = conv.id;
        setConversations((prev) => [conv, ...prev]);
      } catch {
        // 채팅방 생성 실패 - 이 화면 자체엔 에러 버블을 보여줄 메시지 목록이
        // 없으므로(아직 /c/{id}가 아님), 조용히 실패하고 사용자가 다시
        // 전송을 누르게 한다.
        return;
      }
    }

    // 실제 전송(POST + SSE 수신)은 /c/{id} 페이지가 담당 - 여기서는 "보낼
    // 내용"을 세션에 잠깐 맡겨두고 그 페이지로 넘어간다. sessionStorage는
    // 새로고침엔 살아남지만 탭을 닫으면 사라지는, 딱 이 짧은 인계 용도에 맞는
    // 저장소.
    stashPendingMessage(convId, {
      content: text,
      mode,
      size: mode !== "auto" ? size : undefined,
      attachmentIds: readyAttachments.map((a) => a.id),
      attachmentsSnapshot: readyAttachments.map((a) => ({
        id: a.id,
        original_name: a.file.name,
        mime_type: a.file.type || "application/octet-stream",
        size_bytes: a.file.size,
        extraction_status: "done",
        source: "upload",
        created_at: new Date().toISOString(),
      })),
    });

    navigate(`/c/${convId}`, { replace: true });
  }

  return (
    <ChatScreen
      input={input}
      onInputChange={setInput}
      onSend={handleSend}
      disabled={isUploadingAttachment}
      attachments={attachments}
      onFilesSelected={handleFilesSelected}
      onRemoveAttachment={handleRemoveAttachment}
      onAttachmentChipClick={() => {
        // "/"에는 아직 첨부 뷰어를 열 메시지 컨텍스트가 없다 - 굳이 필요하면
        // /c/{id}로 넘어간 뒤(전송 후) 채팅 화면의 뷰어를 쓰면 된다.
      }}
      mode={mode}
      size={size}
      onModelSelect={(m, s) => {
        setMode(m);
        setSize(s);
      }}
    >
      <main className="flex-1 overflow-y-auto">
        <div className="mx-auto flex max-w-2xl flex-col gap-5 px-4 py-6">
          <EmptyState onPick={(text) => setInput(text)} />
        </div>
      </main>
    </ChatScreen>
  );
}
