/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx,ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // Brand orange (logo mark) — used across CTAs, badges, and accents.
        brand: {
          DEFAULT: '#f97316',
          deep: '#ea580c',
        },
      },
      fontFamily: {
        display: ['Satoshi', 'system-ui', 'sans-serif'],
        body: ['"DM Sans"', 'system-ui', 'sans-serif'],
      },
    },
  },
  plugins: [],
};
