/// <reference types="vite/client" />

// The Motion Canvas Vite plugin resolves these query suffixes at build time.
// TypeScript cannot see through them, so they are declared here.

/** `?scene` yields a full scene description. */
declare module '*?scene' {
  const scene: import('@motion-canvas/core').FullSceneDescription
  export default scene
}

/** `?project` wraps makeProject's settings in bootstrap() and yields a
 *  fully constructed Project, complete with logger and meta files. */
declare module '*?project' {
  const project: import('@motion-canvas/core').Project
  export default project
}
