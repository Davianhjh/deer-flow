import { cookies, headers } from "next/headers";
import { Toaster } from "sonner";

import { QueryClientProvider } from "@/components/query-client-provider";
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar";
import { CommandPalette } from "@/components/workspace/command-palette";
import { WorkspaceSidebar } from "@/components/workspace/workspace-sidebar";

function parseSidebarOpenCookie(
  value: string | undefined,
): boolean | undefined {
  if (value === "true") return true;
  if (value === "false") return false;
  return undefined;
}

function isMobileUA(userAgent: string | null): boolean {
  if (!userAgent) return false;
  return /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(
    userAgent,
  );
}

export async function WorkspaceContent({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  const cookieStore = await cookies();
  const cookieSidebar = parseSidebarOpenCookie(
    cookieStore.get("sidebar_state")?.value,
  );

  const headersList = await headers();
  const ua = headersList.get("user-agent");
  const mobile = isMobileUA(ua);

  // Mobile: always start collapsed, cookie ignored
  // Desktop: respect cookie, default to expanded
  const defaultOpen = mobile ? false : (cookieSidebar ?? true);

  return (
    <QueryClientProvider>
      <SidebarProvider className="h-screen" defaultOpen={defaultOpen}>
        <WorkspaceSidebar />
        <SidebarInset className="min-w-0">{children}</SidebarInset>
      </SidebarProvider>
      <CommandPalette />
      <Toaster position="top-center" />
    </QueryClientProvider>
  );
}
