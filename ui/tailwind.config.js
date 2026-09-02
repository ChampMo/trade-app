/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#171B22",
        paper: "#EEF1F5",
        surface: "#FFFFFF",
        line: "#D3DBE4",
        muted: "#5B6675",
        pos: "#0E6E48",
        neg: "#AB2129",
        warn: "#8A5D00",
        demo: "#2B5FD9",
        live: "#B3261E",
      },
      fontFamily: {
        sans: ['"IBM Plex Sans Thai"', "system-ui", "sans-serif"],
        mono: ['"IBM Plex Mono"', "ui-monospace", "monospace"],
      },
    },
  },
  plugins: [],
};
