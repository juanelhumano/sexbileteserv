import socketio
import eventlet
import random

# Crear servidor Socket.IO permitiendo conexiones desde cualquier sitio (CORS)
sio = socketio.Server(cors_allowed_origins='*')
app = socketio.WSGIApp(sio)

# Almacenamiento en memoria (diccionario de salas)
# Estructura: rooms[room_id] = { players: [], config: {}, game_state: {} }
rooms = {}

# Actualizado con 10 de Corazones y 9 de Picas
CARAS_DADOS = ['A', 'K', 'Q', 'J', '10♥', '9♠']

def get_initial_dice():
    return [random.choice(CARAS_DADOS) for _ in range(5)]

@sio.event
def connect(sid, environ):
    print(f'Cliente conectado: {sid}')

@sio.event
def disconnect(sid):
    print(f'Cliente desconectado: {sid}')
    # Buscar en qué sala estaba y eliminarlo
    for room_id, room in rooms.items():
        for player in room['players']:
            if player['id'] == sid:
                room['players'].remove(player)
                sio.emit('update_room', room['players'], room=room_id)
                if len(room['players']) == 0:
                    del rooms[room_id] # Borrar sala si está vacía
                break

@sio.event
def create_room(sid, data):
    room_id = data['room_id']
    username = data['username']
    max_rolls = int(data.get('max_rolls', 3)) # Configuración de tiradas

    if room_id in rooms:
        sio.emit('error', {'message': 'La sala ya existe'}, room=sid)
        return

    sio.enter_room(sid, room_id)
    rooms[room_id] = {
        'players': [{'id': sid, 'name': username, 'score': 0}],
        'config': {'max_rolls': max_rolls},
        'game_active': False,
        'current_turn_index': 0,
        'dice': ['?', '?', '?', '?', '?'],
        'rolls_left': max_rolls,
        'held_indices': [] # Índices de dados guardados
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
    rooms[room_id]['players'].append({'id': sid, 'name': username, 'score': 0})
    
    sio.emit('room_joined', {'room_id': room_id, 'is_host': False, 'config': rooms[room_id]['config']}, room=sid)
    sio.emit('update_room', rooms[room_id]['players'], room=room_id)

@sio.event
def start_game(sid, room_id):
    if room_id in rooms and rooms[room_id]['players'][0]['id'] == sid:
        room = rooms[room_id]
        room['game_active'] = True
        room['current_turn_index'] = 0
        room['dice'] = ['?', '?', '?', '?', '?']
        room['rolls_left'] = room['config']['max_rolls']
        room['held_indices'] = []
        
        # Notificar a todos que el juego empieza
        sio.emit('game_started', {
            'current_turn': room['players'][0]['id'],
            'dice': room['dice'],
            'rolls_left': room['rolls_left']
        }, room=room_id)

@sio.event
def roll_dice(sid, data):
    room_id = data['room_id']
    held_indices = data.get('held_indices', []) # Lista de índices [0, 2, 4]
    
    room = rooms.get(room_id)
    
    # Validaciones
    if not room or not room['game_active']: return
    if room['players'][room['current_turn_index']]['id'] != sid: return
    if room['rolls_left'] <= 0: return

    # Lógica de tirada
    new_dice = []
    for i in range(5):
        if i in held_indices and room['dice'][i] != '?':
            new_dice.append(room['dice'][i]) # Mantener dado
        else:
            new_dice.append(random.choice(CARAS_DADOS)) # Tirar nuevo

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

    # Siguiente turno
    next_idx = (room['current_turn_index'] + 1) % len(room['players'])
    room['current_turn_index'] = next_idx
    
    # Resetear estado para el siguiente jugador
    room['dice'] = ['?', '?', '?', '?', '?']
    room['rolls_left'] = room['config']['max_rolls']
    room['held_indices'] = []

    sio.emit('turn_change', {
        'current_turn': room['players'][next_idx]['id'],
        'last_player_name': room['players'][room['current_turn_index']-1]['name'], # Nombre del que terminó
        'rolls_left': room['rolls_left']
    }, room=room_id)

if __name__ == '__main__':
    eventlet.wsgi.server(eventlet.listen(('', 5000)), app)
