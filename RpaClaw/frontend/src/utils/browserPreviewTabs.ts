export interface BrowserPreviewTab {
  tab_id: string;
  title: string;
  url: string;
  opener_tab_id?: string | null;
  status: string;
  active: boolean;
}

export const resolveActiveBrowserTabId = (
  tabs: BrowserPreviewTab[],
  previousActiveTabId: string | null,
): string | null => {
  const explicitActive = tabs.find((tab) => tab.active);
  if (explicitActive) return explicitActive.tab_id;

  if (previousActiveTabId && tabs.some((tab) => tab.tab_id === previousActiveTabId)) {
    return previousActiveTabId;
  }

  return tabs[0]?.tab_id || null;
};

export const getBrowserPreviewTabLabel = (tab: BrowserPreviewTab): string => (
  tab.title || tab.url || 'New Tab'
);
