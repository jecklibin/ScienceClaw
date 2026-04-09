export class PageAliases {
  #pageIds = new Map<string, string>();
  #aliasCount = 0;

  assign(pageId: string): string {
    const existing = this.#pageIds.get(pageId);
    if (existing) {
      return existing;
    }

    const alias = this.#aliasCount === 0 ? 'page' : `page${this.#aliasCount + 1}`;
    this.#pageIds.set(pageId, alias);
    this.#aliasCount += 1;
    return alias;
  }
}
