import React from 'react';
import ReactDOM from 'react-dom/client';
import '@xyflow/react/dist/style.css';
import './style.css';
import './appearance.css';
import {initializeAppearance} from './appearance';
import App from './App';
initializeAppearance();
ReactDOM.createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);
