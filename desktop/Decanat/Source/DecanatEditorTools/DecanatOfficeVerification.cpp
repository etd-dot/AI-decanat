#include "DecanatMetaHumanTools.h"
#include "../Decanat/DecanatAvatar.h"
#include "../Decanat/DecanatClient.h"
#include "../Decanat/DecanatFaceAnimInstance.h"
#include "Editor.h"
#include "Engine/Blueprint.h"
#include "Engine/SimpleConstructionScript.h"
#include "Engine/SCS_Node.h"
#include "Engine/SkeletalMesh.h"
#include "Engine/GameInstance.h"
#include "EngineUtils.h"
#include "Animation/AnimSingleNodeInstance.h"
#include "Components/SkeletalMeshComponent.h"
#include "Kismet2/KismetEditorUtilities.h"
#include "Dom/JsonObject.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "AnimNodes/AnimNode_ModifyCurve.h"
#include "AnimNodes/AnimNode_PoseBlendNode.h"
#include "Animation/PoseAsset.h"
#include "UObject/UnrealType.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "UnrealClient.h"
#include "PlayInEditorDataTypes.h"
#include "Components/AudioComponent.h"
#if DECANAT_WITH_ACE
#include "ACEBlueprintLibrary.h"
#include "ACEAudioCurveSourceComponent.h"
#endif

bool UDecanatMetaHumanTools::InstallOfficeOutfit(UObject* Asset, UObject* MeshAsset)
{
    auto* Blueprint = Cast<UBlueprint>(Asset);
    auto* Mesh = Cast<USkeletalMesh>(MeshAsset);
    if (!Blueprint || !Blueprint->SimpleConstructionScript || !Mesh ||
        !Blueprint->GetPathName().StartsWith(TEXT("/Game/Decanat/Characters/"))) return false;
    int32 Updated = 0;
    for (auto* Node : Blueprint->SimpleConstructionScript->GetAllNodes()) {
        auto* Component = Cast<USkeletalMeshComponent>(Node->ComponentTemplate);
        if (!Component || !Component->GetSkeletalMeshAsset()) continue;
        const FString Current = Component->GetSkeletalMeshAsset()->GetPathName();
        if (Current.Contains(TEXT("/Clothing/")) || Current.Contains(TEXT("/Wardrobe/"))) {
            Component->Modify();
            Component->SetSkeletalMeshAsset(Mesh);
            Component->EmptyOverrideMaterials();
            ++Updated;
        }
    }
    if (Updated != 1) {
        UE_LOG(LogTemp, Error, TEXT("Expected one outfit component in office Blueprint; found %d"), Updated);
        return false;
    }
    FKismetEditorUtilities::CompileBlueprint(Blueprint);
    Blueprint->MarkPackageDirty();
    return true;
}

bool UDecanatMetaHumanTools::SendPIEMotion(const FString& Action)
{
    if (!GEditor || !GEditor->PlayWorld || (Action != TEXT("stand") && Action != TEXT("sit"))) return false;
    auto* Game = GEditor->PlayWorld->GetGameInstance();
    auto* Client = Game ? Game->GetSubsystem<UDecanatClient>() : nullptr;
    if (!Client || Client->bConnected) return false; // The isolated probe must not control a live conversation.
    auto Payload = MakeShared<FJsonObject>();
    Payload->SetStringField(TEXT("action"), Action);
    Client->OnEvent.Broadcast(TEXT("avatar"), TEXT(""), Payload);
    return true;
}

FString UDecanatMetaHumanTools::InspectPIEMotion()
{
    auto Report = MakeShared<FJsonObject>();
    Report->SetBoolField(TEXT("play_world"), GEditor && GEditor->PlayWorld);
    if (GEditor && GEditor->PlayWorld) {
        for (TActorIterator<ADecanatAvatar> It(GEditor->PlayWorld); It; ++It) {
            Report->SetStringField(TEXT("wrapper_transform"), It->GetActorTransform().ToString());
            Report->SetStringField(TEXT("wrapper_root"), It->GetRootComponent() ? It->GetRootComponent()->GetPathName() : TEXT("none"));
            Report->SetBoolField(TEXT("avatar_configured"), It->IsConfigured());
            Report->SetStringField(TEXT("configuration_issue"), It->GetConfigurationIssue());
        }
        for (TActorIterator<AActor> It(GEditor->PlayWorld); It; ++It) {
            TArray<USkeletalMeshComponent*> Meshes;
            It->GetComponents(Meshes);
            for (auto* Mesh : Meshes) {
                if (Mesh->GetFName() != FName(TEXT("Body"))) continue;
                Report->SetStringField(TEXT("character_transform"), It->GetActorTransform().ToString());
                Report->SetStringField(TEXT("body_transform"), Mesh->GetComponentTransform().ToString());
                auto* Player = Mesh->GetSingleNodeInstance();
                auto* Animation = Player ? Player->GetAnimationAsset() : nullptr;
                Report->SetStringField(TEXT("animation"), Animation ? Animation->GetPathName() : TEXT(""));
                Report->SetNumberField(TEXT("position"), Player ? Player->GetCurrentTime() : -1);
                Report->SetObjectField(TEXT("bones"), MakeShared<FJsonObject>());
                auto Reader = TJsonReaderFactory<>::Create(InspectPose(*It));
                TSharedPtr<FJsonObject> Pose;
                if (FJsonSerializer::Deserialize(Reader, Pose) && Pose) Report->SetObjectField(TEXT("bones"), Pose);
            }
        }
    }
    FString Result;
    FJsonSerializer::Serialize(Report, TJsonWriterFactory<>::Create(&Result));
    return Result;
}

FString UDecanatMetaHumanTools::InspectPIEFace()
{
    auto Report = MakeShared<FJsonObject>();
    Report->SetBoolField(TEXT("play_world"), GEditor && GEditor->PlayWorld);
    if (GEditor && GEditor->PlayWorld) {
        for (TActorIterator<AActor> It(GEditor->PlayWorld); It; ++It) {
            TArray<USkeletalMeshComponent*> Meshes;
            It->GetComponents(Meshes);
            for (auto* Mesh : Meshes) {
                if (Mesh->GetFName() != FName(TEXT("Face"))) continue;
                auto* Animation = Mesh->GetAnimInstance();
                auto* Blink = Cast<UDecanatFaceAnimInstance>(Animation);
                Report->SetStringField(TEXT("animation_class"), Animation ? Animation->GetClass()->GetPathName() : TEXT(""));
#if DECANAT_WITH_ACE
                TArray<TSharedPtr<FJsonValue>> Providers;
                for (const FName Name : UACEBlueprintLibrary::GetAvailableA2FProviderNames()) Providers.Add(MakeShared<FJsonValueString>(Name.ToString()));
                Report->SetArrayField(TEXT("providers"), Providers);
                if (auto* Consumer = It->FindComponentByClass<UACEAudioCurveSourceComponent>()) {
                    Report->SetNumberField(TEXT("consumer_volume"), Consumer->Volume);
                    TArray<float> Weights;
                    Consumer->GetCurveOutputsInterp(Weights);
                    Report->SetNumberField(TEXT("ace_weight_count"), Weights.Num());
                    double Energy = 0;
                    for (const float Weight : Weights) Energy += FMath::Abs(Weight);
                    Report->SetNumberField(TEXT("ace_weight_energy"), Energy);
                    for (int32 Index = 0; Index < Weights.Num() && Index < 55; ++Index)
                        if (UACEAudioCurveSourceComponent::CurveNames[Index] == FName(TEXT("JawOpen")))
                            Report->SetNumberField(TEXT("ace_jaw_open"), Weights[Index]);
                    if (auto* Property = FindFProperty<FObjectPropertyBase>(Consumer->GetClass(), TEXT("AudioComponent"))) {
                        auto* Audio = Cast<UAudioComponent>(Property->GetObjectPropertyValue_InContainer(Consumer));
                        Report->SetBoolField(TEXT("ace_audio_playing"), Audio && Audio->IsPlaying());
                    }
                }
#endif
                Report->SetBoolField(TEXT("native_face_instance"), Blink != nullptr);
                Report->SetNumberField(TEXT("idle_blink"), Blink ? Blink->IdleBlink : -1);
                Report->SetNumberField(TEXT("animation_clock"), Blink ? Blink->GetClock() : -1);
                Report->SetBoolField(TEXT("tick_enabled"), Mesh->IsComponentTickEnabled());
                Report->SetBoolField(TEXT("audio_playback_allowed"), Mesh->GetWorld()->AllowAudioPlayback());
                Report->SetBoolField(TEXT("paused"), Mesh->bPauseAnims);
                Report->SetBoolField(TEXT("force_reference_pose"), Mesh->bForceRefpose);
                Report->SetBoolField(TEXT("no_skeleton_update"), Mesh->bNoSkeletonUpdate);
                Report->SetBoolField(TEXT("curves_allowed"), Mesh->GetAllowedAnimCurveEvaluate());
                Report->SetStringField(TEXT("leader_pose"), Mesh->LeaderPoseComponent.IsValid() ? Mesh->LeaderPoseComponent->GetName() : TEXT(""));
                Report->SetNumberField(TEXT("arkit_blink"), Animation ? Animation->GetCurveValue(TEXT("EyeBlinkLeft")) : -1);
                Report->SetNumberField(TEXT("riglogic_blink"), Animation ? Animation->GetCurveValue(TEXT("CTRL_expressions_eyeBlinkL")) : -1);
                Report->SetNumberField(TEXT("riglogic_jaw_open"), Animation ? Animation->GetCurveValue(TEXT("CTRL_expressions_jawOpen")) : -1);
                const FVector Jaw = Mesh->GetSocketTransform(TEXT("head"), RTS_World).InverseTransformPosition(Mesh->GetBoneLocation(TEXT("FACIAL_C_Jaw")));
                TArray<TSharedPtr<FJsonValue>> JawPosition;
                for (const double Value : {Jaw.X, Jaw.Y, Jaw.Z}) JawPosition.Add(MakeShared<FJsonValueNumber>(Value));
                Report->SetArrayField(TEXT("jaw_bone"), JawPosition);
                auto* Post = Mesh->GetPostProcessInstance();
                Report->SetNumberField(TEXT("postprocess_blink"), Post ? Post->GetCurveValue(TEXT("CTRL_expressions_eyeBlinkL")) : -1);
                if (Animation) for (TFieldIterator<FStructProperty> Field(Animation->GetClass()); Field; ++Field) {
                    if (Field->Struct == FAnimNode_ModifyCurve::StaticStruct()) {
                        auto* Data = Field->ContainerPtrToValuePtr<FAnimNode_ModifyCurve>(Animation);
                        Report->SetNumberField(TEXT("modify_alpha"), Data->Alpha);
                        Report->SetNumberField(TEXT("modify_blink"), Data->CurveValues.Num() ? Data->CurveValues[0] : -1);
                    }
                    if (Field->Struct == FAnimNode_PoseBlendNode::StaticStruct()) {
                        auto* Data = Field->ContainerPtrToValuePtr<FAnimNode_PoseBlendNode>(Animation);
                        Report->SetStringField(TEXT("mapping"), Data->PoseAsset ? Data->PoseAsset->GetPathName() : TEXT(""));
                    }
                }
                auto Bones = MakeShared<FJsonObject>();
                TArray<FName> Names;
                Mesh->GetBoneNames(Names);
                for (const FName Name : Names) {
                    if (!Name.ToString().Contains(TEXT("eyelid"))) continue;
                    const FVector Position = Mesh->GetSocketTransform(TEXT("head"), RTS_World).InverseTransformPosition(Mesh->GetBoneLocation(Name));
                    TArray<TSharedPtr<FJsonValue>> XYZ;
                    for (const double Value : {Position.X, Position.Y, Position.Z}) XYZ.Add(MakeShared<FJsonValueNumber>(Value));
                    Bones->SetArrayField(Name.ToString(), XYZ);
                }
                Report->SetObjectField(TEXT("eyelid_bones"), Bones);
            }
        }
    }
    FString Result;
    FJsonSerializer::Serialize(Report, TJsonWriterFactory<>::Create(&Result));
    return Result;
}

bool UDecanatMetaHumanTools::SendPIEFaceAudio(const FString& PayloadFile)
{
    if (!GEditor || !GEditor->PlayWorld) return false;
    auto* Client = GEditor->PlayWorld->GetGameInstance()->GetSubsystem<UDecanatClient>();
    if (!Client || Client->bConnected) return false;
    FString Text;
    TSharedPtr<FJsonObject> Payload;
    if (!FFileHelper::LoadFileToString(Text, *PayloadFile) ||
        !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text), Payload) || !Payload) return false;
    auto State = MakeShared<FJsonObject>(); State->SetStringField(TEXT("state"), TEXT("speaking"));
    Client->OnEvent.Broadcast(TEXT("state"), TEXT(""), State);
    Client->OnEvent.Broadcast(TEXT("speech"), TEXT(""), Payload);
    return true;
}

bool UDecanatMetaHumanTools::StopPIEFaceAudio()
{
    if (!GEditor || !GEditor->PlayWorld) return false;
    auto* Client = GEditor->PlayWorld->GetGameInstance()->GetSubsystem<UDecanatClient>();
    if (!Client || Client->bConnected) return false;
    Client->OnEvent.Broadcast(TEXT("playback_stop"), TEXT(""), MakeShared<FJsonObject>());
    return true;
}

bool UDecanatMetaHumanTools::CapturePIEFace(bool bClosed)
{
    if (!GEditor || !GEditor->PlayWorld) return false;
    FString Directory = FPaths::ConvertRelativePathToFull(FPaths::ProjectDir() / TEXT("../../.runtime/unreal"));
    FScreenshotRequest::RequestScreenshot(Directory / (bClosed ? TEXT("face-blink-closed.png") : TEXT("face-blink-open.png")), false, false);
    return true;
}

bool UDecanatMetaHumanTools::BeginFaceGame()
{
    if (!GEditor || GEditor->PlayWorld) return false;
    FRequestPlaySessionParams Params;
    Params.SessionDestination = EPlaySessionDestinationType::InProcess;
    Params.WorldType = EPlaySessionWorldType::PlayInEditor;
    GEditor->RequestPlaySession(Params);
    return true;
}

bool UDecanatMetaHumanTools::FinishPIEFaceAudio()
{
    if (!GEditor || !GEditor->PlayWorld) return false;
    auto* Client = GEditor->PlayWorld->GetGameInstance()->GetSubsystem<UDecanatClient>();
    if (!Client || Client->bConnected) return false;
    auto Payload = MakeShared<FJsonObject>(); Payload->SetStringField(TEXT("state"), TEXT("listening"));
    Client->OnEvent.Broadcast(TEXT("state"), TEXT(""), Payload);
    return true;
}
