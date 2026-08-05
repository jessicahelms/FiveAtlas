import { createRoot } from 'react-dom/client';
import App from './App';
import './styles.css';

// No StrictMode: it double-mounts, which fights deck.gl's WebGL context.
createRoot(document.getElementById('root')).render(<App />);
