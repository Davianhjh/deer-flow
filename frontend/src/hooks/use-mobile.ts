import * as React from "react";

const MOBILE_BREAKPOINT = 768;

export function useIsMobile() {
  const [isMobile, setIsMobile] = React.useState<boolean>(() => {
    // SSR-safe initial state: check data-device attribute set server-side
    if (typeof document !== "undefined") {
      return document.documentElement.dataset.device === "mobile";
    }
    return false;
  });

  React.useEffect(() => {
    const mql = window.matchMedia(`(max-width: ${MOBILE_BREAKPOINT - 1}px)`);
    const onChange = () => {
      setIsMobile(window.innerWidth < MOBILE_BREAKPOINT);
    };
    mql.addEventListener("change", onChange);
    // Initial sync: user-agent wins over viewport width for phones
    setIsMobile(
      isMobile ||
        window.innerWidth < MOBILE_BREAKPOINT,
    );
    return () => mql.removeEventListener("change", onChange);
  }, []);

  return isMobile;
}
