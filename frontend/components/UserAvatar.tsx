"use client";

import { useEffect, useState } from "react";
import { getAvatarUrl } from "@/lib/api";
import { User } from "@/lib/types";

// 프로필 사진이 있으면 사진, 없으면(또는 불러오기 실패하면) 이름 첫 글자 원형 아이콘.
// size는 Tailwind 크기 클래스(예: "h-7 w-7"), textSize는 글자 크기 클래스.
export default function UserAvatar({
  user,
  size = "h-7 w-7",
  textSize = "text-xs",
}: {
  user: User | null;
  size?: string;
  textSize?: string;
}) {
  const url = getAvatarUrl(user);
  const [failed, setFailed] = useState(false);

  // 새 사진으로 바뀌면 이전 실패 상태를 풀고 다시 시도한다.
  useEffect(() => {
    setFailed(false);
  }, [url]);

  if (url && !failed) {
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img
        src={url}
        alt=""
        onError={() => setFailed(true)}
        className={`${size} shrink-0 rounded-full object-cover`}
      />
    );
  }
  const initial = (Array.from(user?.name ?? "사용자")[0] ?? "?").toUpperCase();
  return (
    <span
      className={`flex ${size} shrink-0 items-center justify-center rounded-full bg-gray-800 font-semibold text-white dark:bg-gray-100 dark:text-gray-900 ${textSize}`}
    >
      {initial}
    </span>
  );
}
