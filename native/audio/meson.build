project('decanat-audio', 'c', 'cpp', version: '0.1.0', default_options: ['cpp_std=c++17', 'default_library=static', 'b_staticpic=true'])
if host_machine.system() == 'windows' and meson.get_compiler('cpp').get_id() == 'msvc'
  # WebRTC 1.3's LogStreamer DCHECK macros fail under MSVC's strict two-phase
  # lookup. Apply compatibility mode only inside this separate audio build.
  add_global_arguments('/Zc:twoPhase-', language: 'cpp')
endif
upstream = subproject('webrtc-audio-processing-1.3')
apm = upstream.get_variable('audio_processing_dep')
shared_library('decanat_audio', 'src/apm.cpp', dependencies: apm, install: true)
