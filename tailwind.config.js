/** @type {import('tailwindcss').Config} */
module.exports = {
    content: [
        './templates/**/*.html',
        './static/**/*.js',
    ],
    theme: {
        extend: {
            colors: {
                'tn-bg': '#1a1b26',
                'tn-bg-alt': '#24283b',
                'tn-text': '#a9b1d6',
                'tn-comment': '#565f89',
                'tn-blue': '#7aa2f7',
                'tn-purple': '#bb9af7',
                'tn-cyan': '#7dcfff',
                'tn-green': '#9ece6a',
                'tn-yellow': '#e0af68',
                'tn-orange': '#ff9e64',
                'tn-red': '#f7768e',
                'tn-nightBlue': '#414868'
            },
            fontFamily: {
                sans: ['Inter', 'system-ui', 'sans-serif'], // Or your preferred font
            },
        },
    },
    plugins: []
}