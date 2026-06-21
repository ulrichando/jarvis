// @novnc/novnc ships as ESM with no bundled types. Minimal declaration for the
// RFB class we use in components/computer-use/novnc-view.tsx.
declare module "@novnc/novnc" {
  export default class RFB {
    constructor(
      target: HTMLElement,
      url: string,
      options?: { credentials?: { password?: string } },
    );
    scaleViewport: boolean;
    background: string;
    viewOnly: boolean;
    addEventListener(
      type: string,
      cb: (e: { detail?: { clean?: boolean } }) => void,
    ): void;
    disconnect(): void;
  }
}
