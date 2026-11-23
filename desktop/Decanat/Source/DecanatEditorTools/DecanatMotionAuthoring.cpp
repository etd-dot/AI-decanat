#include "DecanatMetaHumanTools.h"
#include "Animation/AnimSequence.h"
#include "Animation/AnimData/IAnimationDataController.h"
#include "Engine/SkeletalMesh.h"
#include "AssetRegistry/AssetRegistryModule.h"
#include "Dom/JsonObject.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "Misc/FileHelper.h"
#include "Misc/PackageName.h"
#include "Components/SkeletalMeshComponent.h"
#include "GameFramework/Actor.h"

namespace {
    TArray<TSharedPtr<FJsonValue>> Numbers(std::initializer_list<double> Values) {
        TArray<TSharedPtr<FJsonValue>> Result;
        for (double Value : Values) Result.Add(MakeShared<FJsonValueNumber>(Value));
        return Result;
    }
}

FString UDecanatMetaHumanTools::InspectPose(UObject* Asset)
{
    auto* Actor = Cast<AActor>(Asset);
    if (!Actor) return TEXT("{}");
    TArray<USkeletalMeshComponent*> Meshes;
    Actor->GetComponents(Meshes);
    auto Report = MakeShared<FJsonObject>();
    for (auto* Mesh : Meshes) {
        if (Mesh->GetFName() != FName(TEXT("Body"))) continue;
        for (const TCHAR* Name : {TEXT("pelvis"), TEXT("foot_l"), TEXT("foot_r"), TEXT("calf_l"), TEXT("calf_r"), TEXT("hand_l"), TEXT("hand_r"), TEXT("lowerarm_l"), TEXT("lowerarm_r"), TEXT("head")}) {
            const FVector Position = Mesh->GetBoneLocation(FName(Name));
            Report->SetArrayField(Name, Numbers({Position.X, Position.Y, Position.Z}));
        }
    }
    FString Result;
    FJsonSerializer::Serialize(Report, TJsonWriterFactory<>::Create(&Result));
    return Result;
}

FString UDecanatMetaHumanTools::ExportSkeleton(UObject* Asset)
{
    auto* Mesh = Cast<USkeletalMesh>(Asset);
    if (!Mesh) return TEXT("{}");
    const FReferenceSkeleton& Skeleton = Mesh->GetRefSkeleton();
    TArray<TSharedPtr<FJsonValue>> Bones;
    for (int32 Index = 0; Index < Skeleton.GetNum(); ++Index) {
        auto Bone = MakeShared<FJsonObject>();
        const FTransform& Local = Skeleton.GetRefBonePose()[Index];
        const FVector Position = Local.GetTranslation();
        const FQuat Rotation = Local.GetRotation();
        const FVector Scale = Local.GetScale3D();
        Bone->SetStringField(TEXT("name"), Skeleton.GetBoneName(Index).ToString());
        Bone->SetNumberField(TEXT("parent"), Skeleton.GetParentIndex(Index));
        Bone->SetArrayField(TEXT("position"), Numbers({Position.X, Position.Y, Position.Z}));
        Bone->SetArrayField(TEXT("rotation"), Numbers({Rotation.X, Rotation.Y, Rotation.Z, Rotation.W}));
        Bone->SetArrayField(TEXT("scale"), Numbers({Scale.X, Scale.Y, Scale.Z}));
        Bones.Add(MakeShared<FJsonValueObject>(Bone));
    }
    auto Report = MakeShared<FJsonObject>();
    Report->SetStringField(TEXT("mesh"), Mesh->GetPathName());
    Report->SetArrayField(TEXT("bones"), Bones);
    FString Result;
    FJsonSerializer::Serialize(Report, TJsonWriterFactory<>::Create(&Result));
    return Result;
}

UObject* UDecanatMetaHumanTools::CreateAnimation(UObject* Asset, const FString& JsonFile)
{
    auto* Mesh = Cast<USkeletalMesh>(Asset);
    FString Text;
    TSharedPtr<FJsonObject> Data;
    if (!Mesh || !Mesh->GetSkeleton() || !FFileHelper::LoadFileToString(Text, *JsonFile) ||
        !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text), Data) || !Data) return nullptr;
    const FString Path = Data->GetStringField(TEXT("asset"));
    const int32 Frames = Data->GetIntegerField(TEXT("frames"));
    const int32 FPS = Data->GetIntegerField(TEXT("fps"));
    if (!Path.StartsWith(TEXT("/Game/Decanat/Animations/")) || Frames < 2 || Frames > 1800 || FPS != 30 ||
        FPackageName::DoesPackageExist(Path) || FindObject<UPackage>(nullptr, *Path)) {
        UE_LOG(LogTemp, Error, TEXT("Animation destination exists or invalid authoring parameters: %s"), *Path);
        return nullptr;
    }
    struct FTrack { FName Bone; TArray<FVector> Positions; TArray<FQuat> Rotations; TArray<FVector> Scales; };
    TArray<FTrack> Tracks;
    for (const auto& Value : Data->GetArrayField(TEXT("tracks"))) {
        auto TrackData = Value->AsObject();
        FTrack Track;
        Track.Bone = FName(*TrackData->GetStringField(TEXT("bone")));
        if (Mesh->GetRefSkeleton().FindBoneIndex(Track.Bone) == INDEX_NONE) return nullptr;
        const auto& Positions = TrackData->GetArrayField(TEXT("positions"));
        const auto& Rotations = TrackData->GetArrayField(TEXT("rotations"));
        if (Positions.Num() != Frames || Rotations.Num() != Frames) return nullptr;
        for (int32 Index = 0; Index < Frames; ++Index) {
            const auto& P = Positions[Index]->AsArray();
            const auto& Q = Rotations[Index]->AsArray();
            if (P.Num() != 3 || Q.Num() != 4) return nullptr;
            FVector Position(P[0]->AsNumber(), P[1]->AsNumber(), P[2]->AsNumber());
            FQuat Rotation(Q[0]->AsNumber(), Q[1]->AsNumber(), Q[2]->AsNumber(), Q[3]->AsNumber());
            if (Position.ContainsNaN() || Rotation.ContainsNaN() || !Rotation.IsNormalized()) return nullptr;
            Track.Positions.Add(Position);
            Track.Rotations.Add(Rotation);
            Track.Scales.Add(FVector::OneVector);
        }
        Tracks.Add(MoveTemp(Track));
    }
    if (Tracks.IsEmpty()) return nullptr;
    UPackage* Package = CreatePackage(*Path);
    auto* Sequence = NewObject<UAnimSequence>(Package, *FPackageName::GetLongPackageAssetName(Path), RF_Public | RF_Standalone);
    Sequence->SetSkeleton(Mesh->GetSkeleton());
    Sequence->SetPreviewMesh(Mesh);
    Sequence->SetRetargetSourceAsset(Mesh);
    Sequence->UpdateRetargetSourceAssetData();
    IAnimationDataController& Controller = Sequence->GetController();
    Controller.InitializeModel();
    Controller.OpenBracket(FText::FromString(TEXT("Author office motion")), false);
    Controller.SetFrameRate(FFrameRate(FPS, 1), false);
    Controller.SetNumberOfFrames(FFrameNumber(Frames - 1), false);
    for (const FTrack& Track : Tracks) {
        Controller.AddBoneCurve(Track.Bone, false);
        Controller.SetBoneTrackKeys(Track.Bone, Track.Positions, Track.Rotations, Track.Scales, false);
    }
    Controller.NotifyPopulated();
    Controller.CloseBracket(false);
    Sequence->MarkPackageDirty();
    FAssetRegistryModule::AssetCreated(Sequence);
    return Sequence;
}
