import {useSyncExternalStore} from 'react';
import {subscribeAppearance,getAppearance,selectedTheme} from './appearance';
export function useAppearance(){const state=useSyncExternalStore(subscribeAppearance,getAppearance);return {...state,theme:selectedTheme(state)};}
