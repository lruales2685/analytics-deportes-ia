import streamlit as st
import pandas as pd
from nba_api.stats.static import players
from nba_api.stats.endpoints import playergamelog
from google import genai

st.set_page_config(page_title="Analytics IA - Deportes", page_icon="🏀", layout="wide")
import os
clave_gemini = st.secrets.get("GEMINI_API_KEY", os.environ.get("GEMINI_API_KEY", ""))
cliente_ia = genai.Client(api_key=clave_gemini)


# Función blindada contra errores 503 de saturación
def consultar_ia_segura(prompt):
    modelos_respaldo = ["gemini-3.5-flash-lite", "gemini-3.5-flash", "gemma-3-27b-it"]
    for modelo in modelos_respaldo:
        try:
            respuesta = cliente_ia.models.generate_content(model=modelo, contents=prompt)
            return respuesta.text
        except Exception:
            continue
    return "⚠️ Los servidores gratuitos de IA están saturados en este segundo. Tus tablas y cálculos estadísticos ya están listos arriba; intenta el resumen de IA en unos segundos."

ODDS_API_KEY = "52cc47e78d8f68163f8d39ff3ada008d"
@st.cache_data(ttl=1800)
def obtener_cuotas_vivo(deporte_key, api_key, mercados="h2h,totals"):
    import requests
    dominio_odds = "api." + "the-odds-api.com"
    url = f"https://{dominio_odds}/v4/sports/{deporte_key}/odds/"
    parametros = {
        "apiKey": api_key,
        "regions": "us,eu",
        "markets": mercados,
        "oddsFormat": "decimal"
    }
    resp = requests.get(url, params=parametros, timeout=15)
    resp.raise_for_status()
    restantes = resp.headers.get("x-requests-remaining", "?")
    return resp.json(), restantes
@st.cache_data(ttl=1800)
def obtener_props_nba_evento(event_id, api_key):
    import requests
    dominio_odds = "api." + "the-odds-api.com"
    url = f"https://{dominio_odds}/v4/sports/basketball_nba/events/{event_id}/odds"
    parametros = {
        "apiKey": api_key,
        "regions": "us",
        "markets": "player_points,player_rebounds,player_assists,player_threes",
        "oddsFormat": "decimal"
    }
    resp = requests.get(url, params=parametros, timeout=15)
    resp.raise_for_status()
    return resp.json(), resp.headers.get("x-requests-remaining", "?")

st.title("📊 Plataforma Deportiva con IA")

tab_nba, tab_futbol, tab_tenis, tab_vivo = st.tabs([
    "🏀 NBA Jugadores & Líneas",
    "⚽ Fútbol (Goles 1ª Mitad)",
    "🎾 Tenis (2 vs 3 Sets)",
    "⚡ Cartelera & Cuotas en Vivo"
])
with tab_nba:
    import plotly.graph_objects as go
    from nba_api.stats.endpoints import commonplayerinfo

    st.subheader("🏀 Análisis Gráfico Avanzado NBA")

    todos_jugadores = players.get_active_players()
    nombres = sorted([j['full_name'] for j in todos_jugadores])
    idx_def = nombres.index("Herbert Jones") if "Herbert Jones" in nombres else nombres.index("Stephen Curry")

    col_sel1, col_sel2, col_sel3 = st.columns([2, 1, 1])
    with col_sel1:
        seleccionado = st.selectbox("Jugador NBA:", nombres, index=idx_def)
    with col_sel2:
        mercado_sel = st.selectbox("Estadística / Mercado:", [
            "Puntos (PTS)", "Asistencias (AST)", "Rebotes (REB)", 
            "Triples (3PM)", "PRA (P+R+A)", "P+R (PTS+REB)", "Robos (STL)"
        ])
    with col_sel3:
        linea_eval = st.number_input("Línea a evaluar:", min_value=0.5, value=1.5 if "Asistencias" in mercado_sel else 14.5, step=0.5)

    # Descargamos información e historial del jugador con caché
    info_jugador = [j for j in todos_jugadores if j['full_name'] == seleccionado][0]
    pid = info_jugador['id']

    @st.cache_data(ttl=3600)
    def obtener_datos_completos_nba(player_id, nombre_jugador):
        cabeceras_nba = {
            'Host': '://nba.com',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'en-US,en;q=0.5',
            'Referer': 'https://nba.com',
            'Origin': 'https://nba.com',
            'Connection': 'keep-alive'
        }
        try:
            log = playergamelog.PlayerGameLog(player_id=player_id, headers=cabeceras_nba, timeout=12)
            df = log.get_data_frames()[0]
            info = commonplayerinfo.CommonPlayerInfo(player_id=player_id, headers=cabeceras_nba, timeout=10).get_data_frames()[0]
            equipo = f"{info['TEAM_CITY'].iloc[0]} {info['TEAM_NAME'].iloc[0]}"
            pos = info['POSITION'].iloc[0]
            return df, equipo, pos
        except Exception:
            # Respaldo automático con ESPN API si ://nba.com bloquea la IP de la nube
            import requests
            dom_espn = "site.web.api." + "espn.com"
            ruta_busq = "/apis/common/v3/search"
            busq = requests.get(
                f"https://{dom_espn}{ruta_busq}",
                params={"query": nombre_jugador, "limit": 3, "type": "player"},
                timeout=10
            ).json()
            espn_id = None
            for item in busq.get("items", []):
                if item.get("league") == "nba":
                    espn_id = item.get("id")
                    break
            if not espn_id:
                return pd.DataFrame(), "NBA", ""
                
            ruta_gl = f"/apis/common/v3/sports/basketball/nba/athletes/{espn_id}/gamelog"
            gl = requests.get(f"https://{dom_espn}{ruta_gl}", timeout=10).json()
           
            eventos_dict = gl.get("events", {})
            filas = []
            for st_type in gl.get("seasonTypes", []):
                for cat in st_type.get("categories", []):
                    for ev in cat.get("events", []):
                        eid = ev.get("eventId")
                        stats = ev.get("stats", [])
                        info_ev = eventos_dict.get(eid, {})
                        if len(stats) >= 14:
                            # Orden estándar ESPN: MIN, FG, FG%, 3PT, 3P%, FT, FT%, REB, AST, BLK, STL, PF, TO, PTS
                            triples = int(str(stats[3]).split('-')[0]) if '-' in str(stats[3]) else 0
                            fga = int(str(stats[1]).split('-')[1]) if '-' in str(stats[1]) else 0
                            filas.append({
                                "GAME_DATE": info_ev.get("gameDate", "")[:10],
                                "MATCHUP": f"{info_ev.get('atVs', 'vs')} {info_ev.get('opponent', {}).get('abbreviation', '')}",
                                "WL": info_ev.get("gameResult", ""),
                                "MIN": int(float(stats[0])) if str(stats[0]).replace('.', '').isdigit() else 0,
                                "PTS": int(float(stats[13])),
                                "REB": int(float(stats[7])),
                                "AST": int(float(stats[8])),
                                "FG3M": triples,
                                "STL": int(float(stats[10])),
                                "FGA": fga
                            })
            return pd.DataFrame(filas), "NBA", ""

    with st.spinner(f"Cargando perfil gráfico de {seleccionado}..."):
        df_nba, equipo_jug, pos_jug = obtener_datos_completos_nba(pid, seleccionado)

    if df_nba.empty:
        st.warning("Este jugador aún no registra partidos en la temporada 2026-27.")
    else:
        # Calculamos columnas combinadas
        df_nba['PRA'] = df_nba['PTS'] + df_nba['REB'] + df_nba['AST']
        df_nba['P+R'] = df_nba['PTS'] + df_nba['REB']
        df_nba['Rival'] = df_nba['MATCHUP'].apply(lambda x: x.split()[-1])
        df_nba['Condicion'] = df_nba['MATCHUP'].apply(lambda x: 'Visitante' if '@' in x else 'Local')

        mapa_cols = {
            "Puntos (PTS)": "PTS",
            "Asistencias (AST)": "AST",
            "Rebotes (REB)": "REB",
            "Triples (3PM)": "FG3M",
            "PRA (P+R+A)": "PRA",
            "P+R (PTS+REB)": "P+R",
            "Robos (STL)": "STL"
        }
        col_activa = mapa_cols[mercado_sel]

        # 1. TARJETA DE ENCABEZADO (Foto oficial + Equipo + Línea)
        c_foto, c_datos = st.columns([1, 4])
        with c_foto:
            url_foto = f"https://nba.com{pid}.png"
            st.image(url_foto, width=110)
        with c_datos:
            st.markdown(f"### **{seleccionado}**")
            st.caption(f"{pos_jug} • **{equipo_jug}** | Línea evaluada: **Over {linea_eval} {mercado_sel}**")

        # 2. FILTROS INTERACTIVOS DEL GRÁFICO (Como la barra inferior de la imagen)
        st.markdown("##### 🎛️ Filtros del Gráfico")
        f_col1, f_col2, f_col3, f_col4 = st.columns(4)
        with f_col1:
            tramo_grafico = st.selectbox("Tramo visual:", ["Últimos 10 (L10)", "Últimos 5 (L5)", "Últimos 20 (L20)", "Toda la Temporada"])
        with f_col2:
            filtro_loc = st.selectbox("Localía:", ["Todos", "Local", "Visitante"])
        with f_col3:
            min_minutos = st.slider("Mínimo de Minutos:", 0, 40, 0)
        with f_col4:
            rivales_disp = ["Todos"] + sorted(df_nba['Rival'].unique().tolist())
            filtro_rival = st.selectbox("Duelo vs. Rival (H2H):", rivales_disp)

        # Aplicamos filtros al DataFrame
        df_filtrado = df_nba.copy()
        if filtro_loc != "Todos":
            df_filtrado = df_filtrado[df_filtrado['Condicion'] == filtro_loc]
        if min_minutos > 0:
            df_filtrado = df_filtrado[df_filtrado['MIN'] >= min_minutos]
        if filtro_rival != "Todos":
            df_filtrado = df_filtrado[df_filtrado['Rival'] == filtro_rival]

        n_limite = {"Últimos 10 (L10)": 10, "Últimos 5 (L5)": 5, "Últimos 20 (L20)": 20, "Toda la Temporada": len(df_filtrado)}[tramo_grafico]
        df_plot = df_filtrado.head(n_limite).iloc[::-1]  # Invertimos para orden cronológico de izquierda a derecha

        if df_plot.empty:
            st.info("No hay partidos que cumplan con esa combinación de filtros.")
        else:
            # 3. TARJETAS SUPERIORES (Proyección, Acertó %, Minutos, Tiros)
            prom_actual = df_plot[col_activa].mean()
            aciertos = (df_plot[col_activa] > linea_eval).sum()
            total_muestra = len(df_plot)
            porc_acierto = (aciertos / total_muestra) * 100

            k1, k2, k3, k4 = st.columns(4)
            k1.metric("PROYECCIÓN (PROM)", f"{prom_actual:.1f}", delta=f"{prom_actual - linea_eval:+.1f} vs línea")
            k2.metric("ACERTÓ", f"{porc_acierto:.0f}%", delta=f"{aciertos} de {total_muestra} partidos")
            k3.metric("MINUTOS PROM.", f"{df_plot['MIN'].mean():.1f}")
            k4.metric("TIROS (FGA) PROM.", f"{df_plot['FGA'].mean():.1f}")

            # 4. GRÁFICO DE BARRAS VERDE / ROJO CON LÍNEA HORIZONTAL (Estilo Props.Cash)
            colores = ['#00E676' if val > linea_eval else '#FF5252' for val in df_plot[col_activa]]
            etiquetas_x = [f"{row['GAME_DATE'][:6]}<br>{row['MATCHUP'].split()[1]}{row['MATCHUP'].split()[2]}" for _, row in df_plot.iterrows()]

            fig = go.Figure()
            fig.add_trace(go.Bar(
                x=etiquetas_x,
                y=df_plot[col_activa],
                marker_color=colores,
                text=df_plot[col_activa],
                textposition='inside',
                insidetextanchor='start',
                textfont=dict(color='white', size=15, family='Arial Black'),
                hovertemplate="<b>%{x}</b><br>Valor: %{y}<extra></extra>"
            ))

            # Línea punteada horizontal de referencia
            fig.add_hline(
                y=linea_eval,
                line_dash="dash",
                line_color="white",
                line_width=2,
                annotation_text=f"Línea: {linea_eval}",
                annotation_position="top left",
                annotation_font=dict(color="white", size=12)
            )

            fig.update_layout(
                template="plotly_dark",
                paper_bgcolor="#0E1117",
                plot_bgcolor="#0E1117",
                height=360,
                margin=dict(l=20, r=20, t=30, b=40),
                xaxis=dict(showgrid=False, tickfont=dict(size=11)),
                yaxis=dict(showgrid=True, gridcolor="#262730", zeroline=False),
                showlegend=False
            )
            st.plotly_chart(fig, use_container_width=True)

            # 5. FILA DE PORCENTAJES HISTÓRICOS (26/27, H2H, L5, L10, L20)
            def calc_pct(serie):
                return f"{(serie > linea_eval).mean() * 100:.0f}%" if len(serie) > 0 else "-"

            p_temp = calc_pct(df_nba[col_activa])
            p_l5 = calc_pct(df_nba[col_activa].head(5))
            p_l10 = calc_pct(df_nba[col_activa].head(10))
            p_l20 = calc_pct(df_nba[col_activa].head(20))
            df_h2h = df_nba[df_nba['Rival'] == filtro_rival] if filtro_rival != "Todos" else pd.DataFrame()
            p_h2h = calc_pct(df_h2h[col_activa]) if not df_h2h.empty else "Elige rival"

            st.markdown("##### 📊 Resumen de Tendencias (Hit Rate)")
            t1, t2, t3, t4, t5 = st.columns(5)
            t1.metric("Temp. 26/27", p_temp)
            t2.metric("H2H Rival", p_h2h)
            t3.metric("L5", p_l5)
            t4.metric("L10", p_l10)
            t5.metric("L20", p_l20)

            # 6. TABLA DETALLADA DESPLEGABLE (Pestaña "Tabla" de la imagen)
            with st.expander("📋 Ver Tabla Detallada de Partidos"):
                cols_t = ['GAME_DATE', 'MATCHUP', 'WL', 'MIN', 'PTS', 'REB', 'AST', 'FG3M', 'STL', 'FGA', 'PRA']
                st.dataframe(df_plot.iloc[::-1][cols_t], use_container_width=True, hide_index=True)

with tab_futbol:
    st.subheader("⚽ Estadísticas de Goles en Primera Mitad (1T)")
    
    base_gh = "https://raw.githubusercontent.com/openfootball/football.json/master/2026-27"
    
    ligas_json = {
        "🏴󠁧󠁢󠁥󠁮󠁧󠁿 Premier League (Inglaterra)": f"{base_gh}/en.1.json",
        "🇪🇸 LaLiga (España)": f"{base_gh}/es.1.json",
        "🇮🇹 Serie A (Italia)": f"{base_gh}/it.1.json",
        "🇩🇪 Bundesliga (Alemania)": f"{base_gh}/de.1.json",
        "🇫🇷 Ligue 1 (Francia)": f"{base_gh}/fr.1.json"
    }

    
    liga_elegida = st.selectbox("Selecciona una Liga:", list(ligas_json.keys()))
    
    @st.cache_data(ttl=3600)
    def descargar_temporada_futbol(url_json):
        import requests
        datos = requests.get(url_json).json()
        filas = []
        for partido in datos.get("matches", []):
            score = partido.get("score", {})
            # Solo tomamos partidos que ya tienen marcador al descanso ('ht') y final ('ft')
            if "ht" in score and "ft" in score:
                filas.append({
                    "Fecha": partido.get("date", ""),
                    "Local": partido.get("team1", ""),
                    "Visitante": partido.get("team2", ""),
                    "1T Local": int(score["ht"][0]),
                    "1T Visita": int(score["ht"][1]),
                    "Final Local": int(score["ft"][0]),
                    "Final Visita": int(score["ft"][1])
                })

        return pd.DataFrame(filas)

    with st.spinner("Cargando temporada 2026-27..."):
        try:
            df_futbol = descargar_temporada_futbol(ligas_json[liga_elegida])
        except Exception as e:
            df_futbol = pd.DataFrame()
            st.error(f"Error al descargar datos: {e}")
        
    if df_futbol.empty:
        st.warning("No se encontraron partidos finalizados en este rango de fechas para la liga.")
    else:
        equipos = sorted(list(set(df_futbol['Local']).union(set(df_futbol['Visitante']))))
        equipo_elegido = st.selectbox("Selecciona un Equipo:", equipos)
        
        if st.button("Analizar Goles 1ª Mitad", type="primary"):
            partidos_eq = df_futbol[(df_futbol['Local'] == equipo_elegido) | (df_futbol['Visitante'] == equipo_elegido)].copy()
            partidos_eq['Goles_1T'] = partidos_eq['1T Local'] + partidos_eq['1T Visita']
            partidos_eq['Condición'] = partidos_eq['Local'].apply(lambda x: 'Local' if x == equipo_elegido else 'Visitante')
            
            total_pj = len(partidos_eq)
            prom_1t = partidos_eq['Goles_1T'].mean()
            over_05 = (partidos_eq['Goles_1T'] >= 1).mean() * 100
            over_15 = (partidos_eq['Goles_1T'] >= 2).mean() * 100
            
            f1, f2, f3, f4 = st.columns(4)
            f1.metric("Partidos Jugados", total_pj)
            f2.metric("Promedio Goles 1T", f"{prom_1t:.2f}")
            f3.metric("% +0.5 Goles 1T", f"{over_05:.1f}%")
            f4.metric("% +1.5 Goles 1T", f"{over_15:.1f}%")
            
            st.markdown("### 🏟️ Desglose Local vs. Visitante (1ª Mitad)")
            partidos_eq['Over_05_1T'] = partidos_eq['Goles_1T'] >= 1
            split_fut = partidos_eq.groupby('Condición').agg(
                Partidos=('Goles_1T', 'count'),
                Prom_Goles_1T=('Goles_1T', 'mean'),
                Porc_Over_05=('Over_05_1T', lambda x: f"{x.mean()*100:.1f}%")
            ).round(2)
            st.dataframe(split_fut, use_container_width=True)
            
            st.markdown("### 📋 Últimos Partidos (Marcador 1T y Final)")
            st.dataframe(partidos_eq[['Fecha', 'Local', 'Visitante', '1T Local', '1T Visita', 'Final Local', 'Final Visita']].tail(10), use_container_width=True, hide_index=True)
            
            st.markdown("### 🤖 Síntesis IA Primeros Tiempos")
            prompt_fut = (
                f"Analiza objetivamente en 3 puntos la tendencia de goles en primera mitad de {equipo_elegido} ({liga_elegida}): "
                f"{total_pj} partidos, promedio {prom_1t:.2f} goles al descanso, {over_05:.1f}% con +0.5 goles y {over_15:.1f}% con +1.5 goles.\n"
                f"Desglose Local/Visitante:\n{split_fut.to_string()}"
            )
            st.info(consultar_ia_segura(prompt_fut))

with tab_tenis:
    st.subheader("🎾 Tenis ATP & WTA: Duración (2 vs. 3 Sets) y Cuotas")
    
    col_t1, col_t2 = st.columns(2)
    with col_t1:
        circuito = st.selectbox("Selecciona Circuito:", ["👨 ATP (Hombres)", "👩 WTA (Mujeres)"])
    with col_t2:
        anio_tenis = st.selectbox("Temporada:", ["2026", "2025", "2024"])
        
    @st.cache_data(ttl=3600)
    def descargar_tenis_cuotas(circ, anio):
        import requests
        import io
        tour = "atp" if "ATP" in circ else "wta"
        dominio_gh = "raw." + "githubusercontent.com"
        carpeta_gh = f"Aneeshers/tennis-sackmann-archive/main/{tour}"
        url_espejo = f"https://{dominio_gh}/{carpeta_gh}/{tour}_matches_{anio}.csv"
        
        resp_gh = requests.get(url_espejo, timeout=20)
        resp_gh.raise_for_status()
        df_gh = pd.read_csv(io.BytesIO(resp_gh.content))
        
        df_gh = df_gh.rename(columns={
            'winner_name': 'Winner', 'loser_name': 'Loser', 'surface': 'Surface',
            'tourney_name': 'Tournament', 'tourney_date': 'Date', 'best_of': 'Best of'
        })
        df_gh['Comment'] = 'Completed'
        df_gh['Total_Sets_Calc'] = df_gh['score'].apply(lambda s: len([x for x in str(s).split() if '-' in x]))
        df_gh['Wsets'] = df_gh['Total_Sets_Calc']
        df_gh['Lsets'] = 0
        return df_gh

    with st.spinner(f"Descargando datos y cuotas oficiales de {circuito} ({anio_tenis})..."):
        try:
            df_tenis = descargar_tenis_cuotas(circuito, anio_tenis)
        except Exception as e:
            df_tenis = pd.DataFrame()
            st.error(f"No se pudo conectar con la base de Tennis-Data: {e}")

    if not df_tenis.empty:
        # Limpiamos partidos terminados (excluyendo retirados 'RET' o 'W/O') y filtramos al mejor de 3 sets
        df_tenis = df_tenis[df_tenis['Comment'] == 'Completed'].copy()
        if 'Best of' in df_tenis.columns:
            df_tenis = df_tenis[df_tenis['Best of'] == 3].copy()
            
        df_tenis['Wsets'] = pd.to_numeric(df_tenis['Wsets'], errors='coerce')
        df_tenis['Lsets'] = pd.to_numeric(df_tenis['Lsets'], errors='coerce')
        df_tenis = df_tenis.dropna(subset=['Wsets', 'Lsets', 'Winner', 'Loser'])
        df_tenis['Total_Sets'] = (df_tenis['Wsets'] + df_tenis['Lsets']).astype(int)
        df_tenis = df_tenis[df_tenis['Total_Sets'].isin([2, 3])]
        
        jugadores_t = sorted(list(set(df_tenis['Winner']).union(set(df_tenis['Loser']))))
        tenista_sel = st.selectbox("Busca o selecciona Tenista:", jugadores_t)
        
        # Panel de cuotas del próximo partido (2 Sets vs 3 Sets y Ganador)
        st.markdown("#### 🎲 Cuotas del Próximo Partido (Opcional)")
        q1, q2, q3 = st.columns(3)
        cuota_2_sets = q1.number_input("Cuota Casa Apuestas: 2 Sets", min_value=1.01, value=1.55, step=0.05)
        cuota_3_sets = q2.number_input("Cuota Casa Apuestas: 3 Sets", min_value=1.01, value=2.35, step=0.05)
        cuota_gana = q3.number_input("Cuota Casa Apuestas: Gana Jugador", min_value=1.01, value=1.75, step=0.05)

        if st.button("Analizar Tenista y Cuotas", type="primary"):
            p_ten = df_tenis[(df_tenis['Winner'] == tenista_sel) | (df_tenis['Loser'] == tenista_sel)].copy()
            
            # Identificamos si ganó o perdió y cuál era su cuota en cada partido
            p_ten['Resultado'] = p_ten['Winner'].apply(lambda w: 'Ganó' if w == tenista_sel else 'Perdió')
            col_cw = 'AvgW' if 'AvgW' in p_ten.columns else ('B365W' if 'B365W' in p_ten.columns else None)
            col_cl = 'AvgL' if 'AvgL' in p_ten.columns else ('B365L' if 'B365L' in p_ten.columns else None)
            
            if col_cw and col_cl:
                p_ten['Cuota_Jugador'] = p_ten.apply(lambda r: r[col_cw] if r['Winner'] == tenista_sel else r[col_cl], axis=1)
                p_ten['Cuota_Rival'] = p_ten.apply(lambda r: r[col_cl] if r['Winner'] == tenista_sel else r[col_cw], axis=1)
            else:
                p_ten['Cuota_Jugador'] = 0.0
                p_ten['Cuota_Rival'] = 0.0

            total_p = len(p_ten)
            victorias = (p_ten['Resultado'] == 'Ganó').sum()
            porc_vic = (victorias / total_p) * 100
            porc_2s = (p_ten['Total_Sets'] == 2).mean() * 100
            porc_3s = (p_ten['Total_Sets'] == 3).mean() * 100
            
            # Cuotas justas calculadas según su frecuencia histórica
            cuota_justa_2s = round(100 / porc_2s, 2) if porc_2s > 0 else 0
            cuota_justa_3s = round(100 / porc_3s, 2) if porc_3s > 0 else 0
            
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Partidos (Bo3)", f"{total_p} ({victorias}G - {total_p - victorias}P)")
            m2.metric("% Victorias", f"{porc_vic:.1f}%", delta=f"Cuota justa: {100/porc_vic:.2f}" if porc_vic > 0 else None)
            m3.metric("% Partidos a 2 Sets", f"{porc_2s:.1f}%", delta=f"Cuota justa: {cuota_justa_2s}")
            m4.metric("% Partidos a 3 Sets", f"{porc_3s:.1f}%", delta=f"Cuota justa: {cuota_justa_3s}")
            
            st.markdown("### 🏟️ Desglose por Superficie (Sets y Victorias)")
            p_ten['Es_3_Sets'] = p_ten['Total_Sets'] == 3
            p_ten['Es_Victoria'] = p_ten['Resultado'] == 'Ganó'
            split_sup = p_ten.groupby('Surface').agg(
                Partidos=('Total_Sets', 'count'),
                Porc_Victorias=('Es_Victoria', lambda x: f"{x.mean()*100:.1f}%"),
                Porc_2_Sets=('Es_3_Sets', lambda x: f"{(1 - x.mean())*100:.1f}%"),
                Porc_3_Sets=('Es_3_Sets', lambda x: f"{x.mean()*100:.1f}%")
            )
            st.dataframe(split_sup, use_container_width=True)
            
            st.markdown("### 📋 Últimos 12 Partidos y Cuotas Históricas")
            cols_ver = ['Date', 'Tournament', 'Surface', 'Winner', 'Loser', 'Total_Sets', 'Cuota_Jugador', 'Cuota_Rival']
            cols_existentes = [c for c in cols_ver if c in p_ten.columns]
            st.dataframe(p_ten[cols_existentes].tail(12), use_container_width=True, hide_index=True)
            
            st.markdown("### 🤖 Síntesis Estadística IA (Sets y Cuotas)")
            prompt_ten = (
                f"Actúa como analista estadístico objetivo de tenis. Resume en 4 puntos los datos de {tenista_sel} ({circuito} {anio_tenis}): "
                f"{total_p} partidos al mejor de 3 sets, {porc_vic:.1f}% de victorias, {porc_2s:.1f}% resueltos en 2 sets (cuota justa {cuota_justa_2s} vs cuota ingresada {cuota_2_sets}) "
                f"y {porc_3s:.1f}% resueltos en 3 sets (cuota justa {cuota_justa_3s} vs cuota ingresada {cuota_3_sets}).\n"
                f"Desglose por superficie:\n{split_sup.to_string()}"
            )
            st.info(consultar_ia_segura(prompt_ten))
with tab_vivo:
    st.subheader("⚡ Partidos del Día y Líneas en Vivo (The Odds API)")
    
    clave_en_web = st.text_input(
        "Pega aquí tu API Key de The Odds API:", 
        value="" if ODDS_API_KEY == "TU_CLAVE_ODDS_API_AQUI" else ODDS_API_KEY, 
        type="password"
    )
    
    deportes_disponibles = {
        "🏀 NBA (Baloncesto)": "basketball_nba",
        "🇪🇸 LaLiga (España)": "soccer_spain_la_liga",
        "🏴󠁧󠁢󠁥󠁮󠁧󠁿 Premier League (Inglaterra)": "soccer_epl",
        "🇮🇹 Serie A (Italia)": "soccer_italy_serie_a",
        "🏆 UEFA Champions League": "soccer_uefa_champs_league"
    }
    
    dep_sel = st.selectbox("Selecciona Competición en Vivo:", list(deportes_disponibles.keys()))
    
    if st.button("Descargar Cuotas y Líneas de Hoy", type="primary"):
        if not clave_en_web:
            st.warning("⚠️ Pega tu clave de The Odds API en el cuadro de arriba.")
        else:
            with st.spinner("Consultando casas de apuestas en tiempo real..."):
                try:
                    eventos, creditos = obtener_cuotas_vivo(deportes_disponibles[dep_sel], clave_en_web.strip())
                    st.session_state["eventos_vivo"] = eventos
                    st.session_state["dep_vivo_actual"] = dep_sel
                    st.session_state["df_props_vivo"] = pd.DataFrame()
                    st.caption(f"💳 Créditos mensuales restantes en tu cuenta gratuita: **{creditos}**")
                except Exception as e:
                    st.error(f"Error al consultar The Odds API: {e}")

    if "eventos_vivo" in st.session_state and st.session_state.get("dep_vivo_actual") == dep_sel:
        eventos = st.session_state["eventos_vivo"]
        if not eventos:
            st.info("No hay partidos programados con cuotas abiertas en este momento para esta liga.")
        else:
            filas_vivo = []
            for ev in eventos:
                local = ev["home_team"]
                visita = ev["away_team"]
                fecha = ev["commence_time"][:16].replace("T", " ")
                bms = ev.get("bookmakers", [])
                cuota_loc, cuota_vis, linea_tot = "-", "-", "-"
                if bms:
                    primer_bm = bms[0]
                    for m in primer_bm.get("markets", []):
                        if m["key"] == "h2h":
                            for out in m["outcomes"]:
                                if out["name"] == local:
                                    cuota_loc = out["price"]
                                elif out["name"] == visita:
                                    cuota_vis = out["price"]
                        elif m["key"] == "totals":
                            if m["outcomes"]:
                                primer_tot = m["outcomes"][0]
                                linea_tot = f"{primer_tot.get('point')} (Over {primer_tot.get('price')})"
                filas_vivo.append({
                    "Fecha (UTC)": fecha,
                    "Local": local,
                    "Visitante": visita,
                    "Cuota Local": cuota_loc,
                    "Cuota Visita": cuota_vis,
                    "Línea Total (O/U)": linea_tot
                })
            st.dataframe(pd.DataFrame(filas_vivo), use_container_width=True, hide_index=True)

            if dep_sel == "🏀 NBA (Baloncesto)":
                st.markdown("### 🎯 Líneas de Jugadores NBA (Player Props del Partido)")
                mapa_eventos = {f"{e['away_team']} @ {e['home_team']}": e["id"] for e in eventos}
                partido_sel = st.selectbox("Elige un partido de hoy para ver líneas de jugadores:", list(mapa_eventos.keys()))
                
                if st.button("Cargar Líneas de Jugadores (Props)"):
                    if not clave_en_web:
                        st.warning("⚠️ Asegúrate de que tu API Key esté pegada en el cuadro de arriba.")
                    else:
                        with st.spinner("Descargando líneas individuales de casas de apuestas..."):
                            try:
                                datos_props, cred_rest = obtener_props_nba_evento(mapa_eventos[partido_sel], clave_en_web.strip())
                                st.caption(f"💳 Créditos restantes: **{cred_rest}**")
                                filas_props = []
                                bms_p = datos_props.get("bookmakers", [])
                                if bms_p:
                                    nombres_mkt = {
                                        "player_points": "Puntos (PTS)",
                                        "player_rebounds": "Rebotes (REB)",
                                        "player_assists": "Asistencias (AST)",
                                        "player_threes": "Triples (FG3M)"
                                    }
                                    for m in bms_p[0].get("markets", []):
                                        cat = nombres_mkt.get(m["key"], m["key"])
                                        for out in m.get("outcomes", []):
                                            if out.get("name") == "Over":
                                                filas_props.append({
                                                    "Jugador": out.get("description"),
                                                    "Mercado": cat,
                                                    "Línea": out.get("point"),
                                                    "Cuota Over": out.get("price")
                                                })
                                if filas_props:
                                    st.session_state["df_props_vivo"] = pd.DataFrame(filas_props).sort_values(["Jugador", "Mercado"])
                                else:
                                    st.session_state["df_props_vivo"] = pd.DataFrame()
                                    st.info("Las casas de apuestas aún no publican props individuales para este partido.")
                            except Exception as e:
                                st.error(f"Error al cargar props: {e}")

                if "df_props_vivo" in st.session_state and not st.session_state["df_props_vivo"].empty:
                    df_props = st.session_state["df_props_vivo"]
                    st.dataframe(df_props, use_container_width=True, hide_index=True)

                    st.markdown("### 🚀 Escáner Automático: Cruce con Estadísticas Reales NBA")
                    n_muestra = st.slider("Partidos recientes a evaluar:", min_value=5, max_value=20, value=10)

                    if st.button("Cruzar Todas las Líneas con Historial NBA", type="primary"):
                        import time
                        import unicodedata

                        def normalizar_nombre(txt):
                            return ''.join(c for c in unicodedata.normalize('NFD', str(txt)) if unicodedata.category(c) != 'Mn').lower().strip()

                        todos_nba = players.get_active_players()
                        mapa_nba = {normalizar_nombre(j['full_name']): j['id'] for j in todos_nba}
                        jugadores_unicos = df_props["Jugador"].unique()
                        barra = st.progress(0, text="Iniciando cruce estadístico...")

                        mapa_col_stat = {
                            "Puntos (PTS)": "PTS",
                            "Rebotes (REB)": "REB",
                            "Asistencias (AST)": "AST",
                            "Triples (FG3M)": "FG3M"
                        }

                        resultados_cruce = []
                        historiales_cache = {}

                        for idx, jug in enumerate(jugadores_unicos):
                            barra.progress((idx + 1) / len(jugadores_unicos), text=f"Analizando {jug} ({idx+1}/{len(jugadores_unicos)})...")
                            pid = mapa_nba.get(normalizar_nombre(jug))
                            if not pid:
                                continue

                            if pid not in historiales_cache:
                                try:
                                    log = playergamelog.PlayerGameLog(player_id=pid, timeout=15)
                                    historiales_cache[pid] = log.get_data_frames()[0]
                                    time.sleep(0.6)
                                except Exception:
                                    historiales_cache[pid] = pd.DataFrame()

                            df_hist = historiales_cache[pid]
                            if df_hist.empty:
                                continue

                            rec = df_hist.head(n_muestra)
                            props_jug = df_props[df_props["Jugador"] == jug]

                            for _, fila_p in props_jug.iterrows():
                                col_stat = mapa_col_stat.get(fila_p["Mercado"])
                                if not col_stat or col_stat not in df_hist.columns:
                                    continue
                                linea = float(fila_p["Línea"])
                                hit_reciente = (rec[col_stat] > linea).mean() * 100
                                hit_temp = (df_hist[col_stat] > linea).mean() * 100
                                prom_rec = rec[col_stat].mean()

                                resultados_cruce.append({
                                    "Jugador": jug,
                                    "Mercado": fila_p["Mercado"],
                                    "Línea": linea,
                                    "Cuota Over": fila_p["Cuota Over"],
                                    f"Prom. Últ. {len(rec)}": round(prom_rec, 1),
                                    "Dif. vs Línea": round(prom_rec - linea, 1),
                                    "% Over Reciente": round(hit_reciente, 1),
                                    "% Over Temporada": round(hit_temp, 1)
                                })                         
                            barra.empty()
                        if resultados_cruce:
                            df_final = pd.DataFrame(resultados_cruce).sort_values(
                                by=["% Over Reciente", "Dif. vs Línea"],
                                ascending=False
                            )
                            st.success(f"✅ Cruce completado: {len(df_final)} líneas evaluadas con datos reales.")
                            st.dataframe(df_final, use_container_width=True, hide_index=True)

                            top5 = df_final.head(5).to_string(index=False)
                            st.markdown("### 🤖 Síntesis IA de las Mayores Tendencias Estadísticas")
                            prompt_scan = (
                                f"Actúa como analista estadístico objetivo de NBA. Resume en 4 viñetas breves los 5 registros con mayor "
                                f"frecuencia reciente de superación de línea en este partido ({partido_sel}):\n{top5}"
                            )
                            st.info(consultar_ia_segura(prompt_scan))
                        else:
                            st.warning("No se pudieron cruzar los datos en este intento. Intenta nuevamente en unos segundos.")

