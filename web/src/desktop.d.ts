export {};

declare global {
  interface Window {
    reasoningDesktop?: Readonly<{
      platform: string;
      revealArtifact: (projectId: string, artifactPath: string) => Promise<void>;
    }>;
  }
}
