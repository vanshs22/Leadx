/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./app/**/*.{js,ts,jsx,tsx}", "./components/**/*.{js,ts,jsx,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        // LeadX Deep Teal / Tangerine identity — premium B2B, not a generic AI gradient.
        brand: {
          50: "#F0F7F2", 100: "#DCEDE3", 200: "#B9DAC8", 300: "#8FC2AA",
          400: "#65A88E", 500: "#3F876F", 600: "#2F6E5B", 700: "#255746",
          800: "#1B4036", 900: "#122E28",
        },
        signal: {
          50: "#FAF2E8", 100: "#F4E3CC", 200: "#E8C39A", 300: "#D7A06C",
          400: "#C4824F", 500: "#A9653E", 600: "#8C4E32", 700: "#713E2B",
          800: "#593024", 900: "#3F221B",
        },
        rust: {
          50: "#FFF1EC", 100: "#FFDCCF", 200: "#FFB9A3", 300: "#FF8E70",
          400: "#F86A49", 500: "#DF4B2F", 600: "#BC3823", 700: "#982D1D",
          800: "#702318", 900: "#4B1811",
        },
        surface: {
          DEFAULT: "rgb(var(--surface) / <alpha-value>)",
          2: "rgb(var(--surface-2) / <alpha-value>)",
          3: "rgb(var(--surface-3) / <alpha-value>)",
        },
        ink: {
          DEFAULT: "rgb(var(--ink) / <alpha-value>)",
          muted: "rgb(var(--ink-muted) / <alpha-value>)",
          faint: "rgb(var(--ink-faint) / <alpha-value>)",
        },
        line: "rgb(var(--line) / <alpha-value>)",
      },
      fontFamily: {
        display: ["var(--font-display)", "ui-sans-serif", "system-ui", "sans-serif"],
        sans: [
          "var(--font-sans)",
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Roboto",
          "Helvetica Neue",
          "Arial",
          "sans-serif",
        ],
        mono: ["var(--font-mono)", "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      boxShadow: {
        card: "0 1px 2px rgb(15 23 42 / .05), 0 10px 30px rgb(15 23 42 / .07)",
        lift: "0 18px 50px rgb(15 23 42 / .14)",
        glow: "0 0 0 1px rgb(22 140 122 / .18), 0 12px 36px rgb(22 140 122 / .15)",
      },
      borderRadius: {
        xl2: "0.625rem",
      },
    },
  },
  plugins: [],
};
