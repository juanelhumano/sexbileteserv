import socketio
import eventlet
import random
from collections import Counter

# Crear servidor Socket.IO
sio = socketio.Server(cors_allowed_origins='*')
app = socketio.WSGIApp(sio)

rooms = {}

# Valores para calcular el ganador
CARAS_DADOS = ['A', 'K', 'Q', 'J', '10♥', '9♠']
VALORES = {'A': 14, 'K': 13, 'Q': 12, 'J': 11, '10♥': 10, '9♠': 9}

def get_initial_dice():
    return [random.choice(CARAS_DADOS) for _ in range(5)]

def get_hand_score(dice):
    """Calcula el valor de la mano."""
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

def resolve_game_over(room, room_id):
    """Función auxiliar para finalizar el juego y anunciar ganador."""
    results = []
    for p in room['players']:
        # Si un jugador no alcanzó a jugar (ej. desconexión masiva), generamos mano random
        if not p['final_hand']:
            p['final_hand'] = [random.choice(CARAS_DADOS) for _ in range(5)]
            
        eval_res = get_hand_score(p['final_hand'])
        results.append({
            'name': p['name'],
            'dice': p['final_hand'],
            'hand_name': eval_res['name'],
            'desc': eval_res['description'],
            'score': eval_res['score_tuple']
        })
    
    # Ordenar por puntaje
    results.sort(key=lambda x: x['score'], reverse=True)
    winner_name = results[0]['name'] if results else "Nadie"

    room['game_active'] = False
    sio.emit('game_over', {
        'results': results,
        'winner_name': winner_name
    }, room=room_id)

@sio.event
def connect(sid, environ):
    print(f'Cliente conectado: {sid}')

@sio.event
def disconnect(sid):
    print(f'Cliente desconectado: {sid}')
    # Iteramos sobre una copia de las llaves para poder borrar salas si es necesario
    for room_id in list(rooms.keys()):
        room = rooms.get(room_id)
        if not room: continue

        # Buscar al jugador y su índice
        player_index = -1
        player_removed = None
        for i, p in enumerate(room['players']):
            if p['id'] == sid:
                player_index = i
                player_removed = p
                break
        
        if player_removed:
            # Eliminar jugador
            room['players'].pop(player_index)
            
            # 1. Si la sala queda vacía, borrarla
            if not room['players']:
                del rooms[room_id]
                break

            # 2. Migración de Anfitrión: Si se fue el índice 0, el nuevo índice 0 es el host
            if player_index == 0:
                new_host = room['players'][0]
                sio.emit('host_promoted', {'is_host': True}, room=new_host['id'])

            # Notificar lista actualizada
            sio.emit('update_room', room['players'], room=room_id)

            # 3. Manejo de desconexión en JUEGO ACTIVO
            if room['game_active']:
                # Ajustar índice de turno si el jugador borrado estaba antes
                if player_index < room['current_turn_index']:
                    room['current_turn_index'] -= 1
                
                # Si el jugador que se fue ERA el del turno actual
                elif player_index == room['current_turn_index']:
                    # Si ya no quedan jugadores suficientes (ej. quedó solo 1), se podría acabar
                    # Pero seguiremos la lógica de pasar turno o terminar ronda
                    
                    # Verificar si se acabó la ronda (era el último o nos pasamos)
                    if room['current_turn_index'] >= len(room['players']):
                        resolve_game_over(room, room_id)
                    else:
                        # Pasar turno al siguiente (que ahora ocupa este mismo índice)
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
        'players': [{'id': sid, 'name': username, 'final_hand': []}],
        'config': {'max_rolls': max_rolls},
        'game_active': False,
        'current_turn_index': 0,
        'dice': ['?', '?', '?', '?', '?'],
        'rolls_left': max_rolls,
        'held_indices': []
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
    rooms[room_id]['players'].append({'id': sid, 'name': username, 'final_hand': []})
    
    sio.emit('room_joined', {'room_id': room_id, 'is_host': False, 'config': rooms[room_id]['config']}, room=sid)
    sio.emit('update_room', rooms[room_id]['players'], room=room_id)

@sio.event
def start_game(sid, room_id):
    # Verificamos que sea el jugador 0 (host)
    if room_id in rooms and rooms[room_id]['players'][0]['id'] == sid:
        room = rooms[room_id]
        room['game_active'] = True
        room['current_turn_index'] = 0
        room['dice'] = ['?', '?', '?', '?', '?']
        room['rolls_left'] = room['config']['max_rolls']
        room['held_indices'] = []
        
        for p in room['players']: p['final_hand'] = []

        sio.emit('game_started', {
            'current_turn': room['players'][0]['id'],
            'current_player_name': room['players'][0]['name'],
            'dice': room['dice'],
            'rolls_left': room['rolls_left']
        }, room=room_id)

@sio.event
def roll_dice(sid, data):
    room_id = data['room_id']
    held_indices = data.get('held_indices', [])
    room = rooms.get(room_id)
    
    if not room or not room['game_active']: return
    if room['players'][room['current_turn_index']]['id'] != sid: return
    if room['rolls_left'] <= 0: return

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

@sio.event
def pass_turn(sid, room_id):
    room = rooms.get(room_id)
    if not room or room['players'][room['current_turn_index']]['id'] != sid: return

    # Guardar mano final
    current_hand = room['dice']
    if '?' in current_hand: 
         current_hand = [random.choice(CARAS_DADOS) for _ in range(5)]

    room['players'][room['current_turn_index']]['final_hand'] = current_hand

    # Verificar fin del juego (si es el último jugador)
    if room['current_turn_index'] >= len(room['players']) - 1:
        resolve_game_over(room, room_id)
    else:
        # Siguiente turno
        next_idx = room['current_turn_index'] + 1
        room['current_turn_index'] = next_idx
        
        room['dice'] = ['?', '?', '?', '?', '?']
        room['rolls_left'] = room['config']['max_rolls']
        room['held_indices'] = []

        sio.emit('turn_change', {
            'current_turn': room['players'][next_idx]['id'],
            'current_player_name': room['players'][next_idx]['name'],
            'last_player_name': room['players'][room['current_turn_index']-1]['name'],
            'rolls_left': room['rolls_left']
        }, room=room_id)

if __name__ == '__main__':
    eventlet.wsgi.server(eventlet.listen(('', 5000)), app)
