import { useEffect, useState } from "react";
import { fetchBlobUrl } from "./api";

/**
 * 인증이 필요한 파일 URL을 <img>/<iframe>에 넣을 수 있는 blob URL로 바꾼다.
 * url이 바뀌거나 컴포넌트가 사라지면 이전 blob URL을 해제한다.
 */
export function useAuthedObjectUrl(url: string | null): { src: string | null; failed: boolean } {
  const [src, setSrc] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    setSrc(null);
    setFailed(false);
    if (!url) return;
    let cancelled = false;
    let created: string | null = null;
    fetchBlobUrl(url)
      .then((blobUrl) => {
        created = blobUrl;
        if (cancelled) URL.revokeObjectURL(blobUrl);
        else setSrc(blobUrl);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
      if (created) URL.revokeObjectURL(created);
    };
  }, [url]);

  return { src, failed };
}
