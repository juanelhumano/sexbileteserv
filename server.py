import socketio
import eventlet
import random
from collections import Counter

# Crear servidor Socket.IO
sio = socketio.Server(cors_allowed_origins='*')
app = socketio.WSGIApp(sio)

rooms = {}
TURN_TIMEOUT = 10  # Segundos para actuar

CARAS_DADOS = ['A', 'K', 'Q', 'J', '10♥', '9♠']
VALORES = {'A': 14, 'K': 13, 'Q': 12, 'J': 11, '10♥': 10, '9♠': 9}

def get_initial_dice():
    return [random.choice(CARAS_DADOS) for _ in range(5)]

def get_hand_score(dice):
    if not dice: return {'score_tuple': (0, []), 'name': 'Nada', 'description': 'Sin jugada'}
    nums = sorted([VALORES[d] for d in dice], reverse=True)
    counts = Counter(nums)
    sorted_counts = sorted(counts.items(), key=lambda x: (x[1], x[0]), reverse=True)
    shape = [c[1] for c in sorted_counts] 
    vals = [c[0] for c in sorted_counts]  
    
    hand_name = "Nada"
    score_type = 0 
    if shape == [5]: score_type = 7; hand_name = "Quintilla (Grande)"
    elif shape == [4, 1]: score_type = 6; hand_name = "Poker"
    elif shape == [3, 2]: score_type = 5; hand_name = "Full House"
    elif shape == [3, 1, 1]: score_type = 4; hand_name = "Tercia"
    elif shape == [2, 2, 1]: score_type = 3; hand_name = "Dos Pares"
    elif shape == [2, 1, 1, 1]: score_type = 2; hand_name = "Par"
    else: score_type = 1; hand_name = "Carta Alta"

    return {
        'score_tuple': (score_type, vals),
        'name': hand_name,
        'description': f"{hand_name} de {get_key_from_value(vals[0])}"
    }

def get_key_from_value(val):
    for k, v in VALORES.items():
        if v == val: return k
    return str(val)

# --- LÓGICA DEL TEMPORIZADOR ---

def start_turn_timer(room_id):
    """Inicia o reinicia el temporizador de turno."""
    room = rooms.get(room_id)
    if not room or not room['game_active']: return
    
    # Incrementamos el ID de acción para invalidar temporizadores anteriores
    room['action_id'] += 1
    current_action_id = room['action_id']
    
    # Programar el timeout
    eventlet.spawn_after(TURN_TIMEOUT, handle_turn_timeout, room_id, current_action_id)

def handle_turn_timeout(room_id, action_id):
    """Se ejecuta cuando se acaba el tiempo."""
    room = rooms.get(room_id)
    if not room or not room['game_active']: return
    
    # Si el action_id cambió, significa que el jugador actuó y este timer es viejo
    if room['action_id'] != action_id: return

    print(f"Timeout en sala {room_id}. Auto-jugando...")
    
    # Identificar jugador actual
    current_player = room['players'][room['current_turn_index']]
    sid = current_player['id']
    
    if room['rolls_left'] > 0:
        # Si le quedan tiros, tiramos todo (held_indices=[]) para avanzar
        execute_roll(sid, room_id, [])
    else:
        # Si no le quedan tiros, pasamos turno
        execute_pass(sid, room_id)


# --- FUNCIONES DE JUEGO (Refactorizadas para ser llamadas por Socket o Timer) ---

def execute_roll(sid, room_id, held_indices):
    room = rooms.get(room_id)
    if not room or not room['game_active']: return
    
    # Validar turno (si viene del timer, sid es correcto por definición, pero validamos igual)
    if room['players'][room['current_turn_index']]['id'] != sid: return
    if room['rolls_left'] <= 0: return # Si intenta tirar sin tiros, forzamos pass? No, solo retornamos.

    new_dice = []
    for i in range(5):
        if i in held_indices and room['dice'][i] != '?':
            new_dice.append(room['dice'][i])
        else:
            new_dice.append(random.choice(CARAS_DADOS))

    room['dice'] = new_dice
    room['rolls_left'] -= 1
    room['held_indices'] = held_indices

    sio.emit('dice_rolled', {
        'dice': new_dice,
        'rolls_left': room['rolls_left'],
        'player_id': sid
    }, room=room_id)
    
    # Reiniciar timer para la siguiente acción
    start_turn_timer(room_id)

def execute_pass(sid, room_id):
    room = rooms.get(room_id)
    if not room: return
    if room['players'][room['current_turn_index']]['id'] != sid: return

    current_hand = room['dice']
    if '?' in current_hand: 
         current_hand = [random.choice(CARAS_DADOS) for _ in range(5)]
    room['players'][room['current_turn_index']]['final_hand'] = current_hand

    if room['current_turn_index'] >= len(room['players']) - 1:
        resolve_game_over(room, room_id)
    else:
        last_player_idx = room['current_turn_index']
        last_player_hand = room['players'][last_player_idx]['final_hand']
        last_hand_eval = get_hand_score(last_player_hand)

        next_idx = room['current_turn_index'] + 1
        room['current_turn_index'] = next_idx
        
        room['dice'] = ['?', '?', '?', '?', '?']
        room['rolls_left'] = room['config']['max_rolls']
        room['held_indices'] = []

        sio.emit('turn_change', {
            'current_turn': room['players'][next_idx]['id'],
            'current_player_name': room['players'][next_idx]['name'],
            'last_player_name': room['players'][next_idx-1]['name'],
            'last_player_hand': last_player_hand, 
            'last_player_desc': last_hand_eval['description'],
            'rolls_left': room['rolls_left']
        }, room=room_id)
        
        # Iniciar timer para el siguiente jugador
        start_turn_timer(room_id)

def resolve_game_over(room, room_id):
    results = []
    for p in room['players']:
        p['is_ready'] = False 
        if not p['final_hand']:
            p['final_hand'] = [random.choice(CARAS_DADOS) for _ in range(5)]
        eval_res = get_hand_score(p['final_hand'])
        results.append({
            'id': p['id'],
            'name': p['name'],
            'dice': p['final_hand'],
            'hand_name': eval_res['name'],
            'desc': eval_res['description'],
            'score': eval_res['score_tuple']
        })
    
    results.sort(key=lambda x: x['score'], reverse=True)
    winner_name = results[0]['name'] if results else "Nadie"

    room['game_active'] = False
    sio.emit('game_over', {
        'results': results,
        'winner_name': winner_name
    }, room=room_id)
    # No reiniciamos timer aquí porque el juego acabó

# --- EVENTOS SOCKET.IO ---

@sio.event
def connect(sid, environ):
    print(f'Cliente conectado: {sid}')

@sio.event
def disconnect(sid):
    print(f'Cliente desconectado: {sid}')
    for room_id in list(rooms.keys()):
        room = rooms.get(room_id)
        if not room: continue

        player_index = -1
        player_removed = None
        for i, p in enumerate(room['players']):
            if p['id'] == sid:
                player_index = i
                player_removed = p
                break
        
        if player_removed:
            room['players'].pop(player_index)
            if not room['players']:
                del rooms[room_id]
                break
            if player_index == 0:
                new_host = room['players'][0]
                sio.emit('host_promoted', {'is_host': True}, room=new_host['id'])

            sio.emit('update_room', room['players'], room=room_id)

            if room['game_active']:
                # Si se va alguien en medio del juego, invalidamos el timer actual
                # (Aunque la lógica de start_turn_timer ya maneja ids nuevos)
                
                if player_index < room['current_turn_index']:
                    room['current_turn_index'] -= 1
                elif player_index == room['current_turn_index']:
                    if room['current_turn_index'] >= len(room['players']):
                        resolve_game_over(room, room_id)
                    else:
                        # Reseteamos estado para el siguiente
                        room['dice'] = ['?', '?', '?', '?', '?']
                        room['rolls_left'] = room['config']['max_rolls']
                        room['held_indices'] = []
                        next_p = room['players'][room['current_turn_index']]
                        sio.emit('turn_change', {
                            'current_turn': next_p['id'],
                            'current_player_name': next_p['name'],
                            'last_player_name': f"{player_removed['name']} (Salió)",
                            'rolls_left': room['rolls_left']
                        }, room=room_id)
                        # Iniciamos timer para el nuevo jugador
                        start_turn_timer(room_id)
            break

@sio.event
def create_room(sid, data):
    room_id = data['room_id']
    username = data['username']
    max_rolls = int(data.get('max_rolls', 3))
    if room_id in rooms:
        sio.emit('error', {'message': 'La sala ya existe'}, room=sid)
        return
    sio.enter_room(sid, room_id)
    rooms[room_id] = {
        'players': [{'id': sid, 'name': username, 'final_hand': [], 'is_ready': True}],
        'config': {'max_rolls': max_rolls},
        'game_active': False,
        'current_turn_index': 0,
        'dice': ['?', '?', '?', '?', '?'],
        'rolls_left': max_rolls,
        'held_indices': [],
        'action_id': 0 # Para controlar timers obsoletos
    }
    sio.emit('room_joined', {'room_id': room_id, 'is_host': True, 'config': rooms[room_id]['config']}, room=sid)
    sio.emit('update_room', rooms[room_id]['players'], room=room_id)

@sio.event
def join_room(sid, data):
    room_id = data['room_id']
    username = data['username']
    if room_id not in rooms:
        sio.emit('error', {'message': 'La sala no existe'}, room=sid)
        return
    if rooms[room_id]['game_active']:
        sio.emit('error', {'message': 'El juego ya comenzó'}, room=sid)
        return
    sio.enter_room(sid, room_id)
    rooms[room_id]['players'].append({'id': sid, 'name': username, 'final_hand': [], 'is_ready': False})
    sio.emit('room_joined', {'room_id': room_id, 'is_host': False, 'config': rooms[room_id]['config']}, room=sid)
    sio.emit('update_room', rooms[room_id]['players'], room=room_id)

@sio.event
def player_ready(sid, room_id):
    room = rooms.get(room_id)
    if not room: return
    for p in room['players']:
        if p['id'] == sid:
            p['is_ready'] = True
            sio.emit('player_status_update', {'player_id': sid, 'is_ready': True}, room=room_id)
            break

@sio.event
def start_game(sid, room_id):
    if room_id in rooms and rooms[room_id]['players'][0]['id'] == sid:
        room = rooms[room_id]
        
        active_players = []
        players_to_remove = []

        for p in room['players']:
            if p['id'] == sid or p.get('is_ready', False):
                active_players.append(p)
            else:
                players_to_remove.append(p)
        
        for p in players_to_remove:
            sio.emit('kicked_inactive', room=p['id'])
            sio.leave_room(p['id'], room_id)

        room['players'] = active_players
        room['game_active'] = True
        room['current_turn_index'] = 0
        room['dice'] = ['?', '?', '?', '?', '?']
        room['rolls_left'] = room['config']['max_rolls']
        room['held_indices'] = []
        
        for p in room['players']: 
            p['final_hand'] = []
            p['is_ready'] = False

        sio.emit('update_room', room['players'], room=room_id)
        sio.emit('game_started', {
            'current_turn': room['players'][0]['id'],
            'current_player_name': room['players'][0]['name'],
            'dice': room['dice'],
            'rolls_left': room['rolls_left']
        }, room=room_id)
        
        # INICIAR TIMER PRIMER TURNO
        start_turn_timer(room_id)

@sio.event
def roll_dice(sid, data):
    # Wrapper simple para el evento
    execute_roll(sid, data['room_id'], data.get('held_indices', []))

@sio.event
def pass_turn(sid, room_id):
    # Wrapper simple para el evento
    execute_pass(sid, room_id)

if __name__ == '__main__':
    eventlet.wsgi.server(eventlet.listen(('', 5000)), app)
